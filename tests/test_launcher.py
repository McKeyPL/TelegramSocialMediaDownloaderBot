import configparser
import os
import signal
import socket
import subprocess
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import Mock, patch

from telebot import apihelper

import bot
import run_bot
from runtime_config import RuntimeSettings, read_config


def config_for(mode=None):
    config = configparser.ConfigParser()
    config.read_dict({"config": {"token": "123:fixture"}})
    if mode:
        config.read_dict({"telegram": {"mode": mode}})
    return config


class LauncherTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="telegram bot test ")
        self.addCleanup(directory.cleanup)
        self.data_dir = Path(directory.name)

    def test_old_config_defaults_to_public_and_preserves_percent_signs(self):
        path = self.data_dir / "config.txt"
        path.write_bytes(b"\xef\xbb\xbf[bluesky]\napp_password = entry%fixture\n")
        config = read_config(path)
        self.assertEqual(config["bluesky"]["app_password"], "entry%fixture")
        self.assertEqual(RuntimeSettings.from_config(config).mode, "public")

    def test_public_bot_resets_endpoint_and_local_requires_launcher(self):
        config = config_for("local")
        with (
            patch.dict(os.environ, {}, clear=True),
            patch.object(bot.telebot, "TeleBot"),
            patch.object(apihelper, "API_URL"),
            patch.object(apihelper, "FILE_URL"),
        ):
            with self.assertRaisesRegex(ValueError, "run_bot.py"):
                bot.create_bot(config)
            os.environ["TSMD_LOCAL_ENDPOINT"] = "http://127.0.0.1:8081"
            bot.create_bot(config)
            self.assertEqual(apihelper.API_URL, "http://127.0.0.1:8081/bot{0}/{1}")
            self.assertEqual(apihelper.FILE_URL, "http://127.0.0.1:8081/file/bot{0}/{1}")
            bot.create_bot(config_for())
            self.assertIsNone(apihelper.API_URL)

    def test_native_server_command_and_private_environment(self):
        config = config_for("local")
        config["telegram"]["local_binary"] = "api server.exe"
        (self.data_dir / "api server.exe").touch()
        with patch.dict(os.environ, {"TELEGRAM_API_ID": "123", "TELEGRAM_API_HASH": "a" * 32}):
            command, env = run_bot.local_server_options(config, RuntimeSettings.from_config(config), self.data_dir)
        self.assertEqual(command[0], str(self.data_dir / "api server.exe"))
        self.assertEqual(command[1:6], ["--local", "--http-ip-address", "127.0.0.1", "--http-port", "8081"])
        self.assertEqual(env["TELEGRAM_API_HASH"], "a" * 32)
        self.assertNotIn("a" * 32, command)
        self.assertFalse((self.data_dir / "telegram-bot-api-data").exists())

    def test_native_server_uses_bundled_binary_even_with_external_runtime_dir(self):
        release = self.data_dir / "release"
        runtime = self.data_dir / "data"
        native_name = "telegram-bot-api.exe" if os.name == "nt" else "telegram-bot-api"
        binary = release / "bin" / native_name
        binary.parent.mkdir(parents=True)
        binary.touch()
        config = config_for("local")
        with (
            patch.object(run_bot, "ROOT", release),
            patch.dict(os.environ, {"TELEGRAM_API_ID": "123", "TELEGRAM_API_HASH": "a" * 32}),
            patch.object(run_bot.shutil, "which") as from_path,
        ):
            command, _ = run_bot.local_server_options(
                config, RuntimeSettings.from_config(config), runtime, prepare=True,
            )
        self.assertEqual(command[0], str(binary))
        self.assertEqual(command[command.index("--dir") + 1], str(runtime / "telegram-bot-api-data"))
        self.assertTrue((runtime / "telegram-bot-api-data" / "tmp").is_dir())
        from_path.assert_not_called()

    def test_occupied_port_is_reported(self):
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            with self.assertRaisesRegex(RuntimeError, "Cannot bind"):
                run_bot.check_port_available(listener.getsockname()[1])

    def test_readiness_requires_bot_api_response(self):
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(404)
                self.end_headers()
                self.wfile.write(self.server.payload)

            def log_message(self, *_args):
                pass

        with HTTPServer(("127.0.0.1", 0), Handler) as server:
            server_thread = threading.Thread(target=server.serve_forever, daemon=True)
            server_thread.start()
            try:
                server.payload = b"ordinary web server"
                self.assertFalse(run_bot.server_is_ready(server.server_port))
                server.payload = b'{"ok": false, "error_code": 404}'
                self.assertTrue(run_bot.server_is_ready(server.server_port))
            finally:
                server.shutdown()
                server_thread.join()

    def test_local_shutdown_stops_bot_before_server_on_ctrl_c(self):
        ready_file = self.data_dir / "ready"
        config = config_for("local")
        settings = RuntimeSettings.from_config(config)
        server = Mock(returncode=None)
        server.poll.return_value = None
        child = Mock(returncode=None)
        child.poll.return_value = None
        order = []
        child_env = {}
        handlers = {}

        def start(command, **kwargs):
            if command[0] == "fixture-api":
                return server
            child_env.update(kwargs["env"])
            return child

        def wait(process, is_ready, _timeout, _stopping, _label):
            if process is child:
                Path(child_env["TSMD_BOT_READY_FILE"]).touch()
            self.assertTrue(is_ready())
            return True

        def stop_by_signal(_delay):
            self.assertTrue(ready_file.exists())
            handlers[signal.SIGINT](signal.SIGINT, None)

        def wait_for_exit(process, _timeout):
            self.assertIs(process, child)
            self.assertTrue(Path(child_env["TSMD_BOT_STOP_FILE"]).exists())
            order.append("bot")
            return True

        with (
            patch.object(run_bot.signal, "signal", side_effect=lambda sig, handler: handlers.update({sig: handler})),
            patch.object(run_bot, "check_port_available"),
            patch.object(run_bot, "local_server_options", return_value=(["fixture-api"], {})),
            patch.object(run_bot, "server_is_ready", return_value=True),
            patch.object(run_bot, "wait_until_ready", side_effect=wait),
            patch.object(run_bot.subprocess, "Popen", side_effect=start) as popen,
            patch.object(run_bot.time, "sleep", side_effect=stop_by_signal),
            patch.object(run_bot, "wait_for_exit", side_effect=wait_for_exit),
            patch.object(run_bot, "stop_child", side_effect=lambda child, *_a, **_k: order.append("server")),
        ):
            self.assertEqual(run_bot.run(config, settings, self.data_dir, ready_file, None), 0)
        self.assertEqual(popen.call_count, 2)
        self.assertEqual(child_env["TSMD_LOCAL_ENDPOINT"], "http://127.0.0.1:8081")
        self.assertNotIn("TELEGRAM_API_HASH", child_env)
        self.assertEqual(order, ["bot", "server"])
        self.assertFalse(ready_file.exists())

    def test_server_start_failure_does_not_start_bot(self):
        config = config_for("local")
        server = Mock(returncode=3)
        server.poll.return_value = 3
        with (
            patch.object(run_bot.signal, "signal"),
            patch.object(run_bot, "check_port_available"),
            patch.object(run_bot, "local_server_options", return_value=(["fixture-api"], {})),
            patch.object(run_bot.subprocess, "Popen", return_value=server) as popen,
        ):
            with self.assertRaisesRegex(RuntimeError, "exited during startup"):
                run_bot.run(config, RuntimeSettings.from_config(config), self.data_dir, None, None)
        popen.assert_called_once()

    def test_public_starts_only_bot_and_accepts_stop_marker(self):
        config = config_for()
        stop_file = self.data_dir / "stop"
        child = Mock(returncode=None)
        child.poll.return_value = None
        with (
            patch.dict(os.environ, {"TSMD_LOCAL_ENDPOINT": "http://127.0.0.1:9"}),
            patch.object(run_bot.signal, "signal"),
            patch.object(run_bot.subprocess, "Popen", return_value=child) as popen,
            patch.object(run_bot, "local_server_options") as server_options,
            patch.object(run_bot, "wait_until_ready", return_value=True),
            patch.object(run_bot, "wait_for_exit", return_value=True),
            patch.object(run_bot.time, "sleep", side_effect=lambda _: stop_file.touch()),
        ):
            self.assertEqual(run_bot.run(config, RuntimeSettings.from_config(config), self.data_dir, None, stop_file), 0)
        self.assertNotIn("TSMD_LOCAL_ENDPOINT", popen.call_args.kwargs["env"])
        popen.assert_called_once()
        server_options.assert_not_called()

    def test_runtime_timeout_is_bounded(self):
        child = Mock(returncode=None)
        child.poll.return_value = None
        with patch.object(run_bot.time, "monotonic", side_effect=[5, 6]):
            with self.assertRaisesRegex(RuntimeError, "within 1 seconds"):
                run_bot.wait_until_ready(child, lambda: False, 1, lambda: False, "fixture")

    def test_logout_is_explicit_and_does_not_start_polling(self):
        config = config_for()
        settings = RuntimeSettings.from_config(config)
        with (
            patch.object(run_bot.signal, "signal"),
            patch.object(run_bot, "logout_bot") as logout,
            patch.object(run_bot.subprocess, "Popen") as popen,
        ):
            self.assertEqual(run_bot.run(config, settings, self.data_dir, None, None, logout=True), 0)
        logout.assert_called_once_with("123:fixture", settings)
        popen.assert_not_called()

    def test_forces_exit_if_child_does_not_stop(self):
        child = Mock()
        child.poll.return_value = None
        child.wait.side_effect = [subprocess.TimeoutExpired("fixture", 1), 0]
        run_bot.stop_child(child, 1)
        child.terminate.assert_called_once()
        child.kill.assert_called_once()
