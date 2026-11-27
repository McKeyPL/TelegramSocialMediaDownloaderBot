#!/usr/bin/env python3
"""Start the bot and, in local mode, its dedicated Telegram API server."""

import argparse
import configparser
import http.client
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from media_policy import MediaPolicy
from native_release import install_native_api
from runtime_config import RuntimeSettings, read_config


ROOT = Path(__file__).resolve().parent
LOOPBACK = "127.0.0.1"


def local_server_options(config, settings, data_dir, *, prepare=False):
    api_id = os.environ.get("TELEGRAM_API_ID", config.get("telegram", "api_id", fallback="")).strip()
    api_hash = os.environ.get("TELEGRAM_API_HASH", config.get("telegram", "api_hash", fallback="")).strip()
    if not re.fullmatch(r"[1-9][0-9]{0,9}", api_id) or int(api_id) > 2147483647:
        raise ValueError("Configure a valid Telegram api_id")
    if not re.fullmatch(r"[a-fA-F0-9]{32}", api_hash):
        raise ValueError("Configure a valid Telegram api_hash")

    configured_binary = config.get("telegram", "local_binary", fallback="").strip()
    if configured_binary:
        binary = data_dir / Path(configured_binary).expanduser()
        executable = str(binary) if binary.is_file() else None
    else:
        native_name = "telegram-bot-api.exe" if os.name == "nt" else "telegram-bot-api"
        bundled_binary = ROOT / "bin" / native_name
        executable = str(bundled_binary) if bundled_binary.is_file() else shutil.which("telegram-bot-api")
        if executable is None:
            executable = str(install_native_api(ROOT, allow_download=prepare))
    if not executable:
        raise ValueError("Use the release for this platform or set telegram.local_binary")

    directory = config.get("telegram", "local_data_dir", fallback="").strip() or "telegram-bot-api-data"
    server_dir = data_dir / Path(directory).expanduser()
    server_temp = server_dir / "tmp"
    if prepare:
        server_temp.mkdir(parents=True, exist_ok=True)
    command = [
        executable, "--local", "--http-ip-address", LOOPBACK, "--http-port", str(settings.port),
        "--dir", str(server_dir), "--temp-dir", str(server_temp),
    ]
    env = {**os.environ, "TELEGRAM_API_ID": api_id, "TELEGRAM_API_HASH": api_hash}
    return command, env


def check_port_available(port):
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            if os.name == "nt":
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            else:
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind((LOOPBACK, port))
    except OSError as exc:
        raise RuntimeError(f"Cannot bind local Telegram Bot API to {LOOPBACK}:{port}") from exc


def server_is_ready(port):
    connection = http.client.HTTPConnection(LOOPBACK, port, timeout=1)
    try:
        connection.request("GET", "/")
        response = connection.getresponse()
        if response.status != 404:
            return False
        result = json.loads(response.read(4096))
        return isinstance(result, dict) and result.get("ok") is False and result.get("error_code") == 404
    except (OSError, http.client.HTTPException, ValueError):
        return False
    finally:
        connection.close()


def logout_bot(token, settings):
    if settings.mode == "local":
        connection = http.client.HTTPConnection(LOOPBACK, settings.port, timeout=30)
    else:
        connection = http.client.HTTPSConnection("api.telegram.org", timeout=30)
    try:
        connection.request("POST", f"/bot{token}/logOut", body=b"")
        response = connection.getresponse()
        result = json.loads(response.read(4096))
        if response.status != 200 or not isinstance(result, dict) or result.get("result") is not True:
            raise RuntimeError(f"Telegram rejected logOut (HTTP {response.status})")
    except (OSError, http.client.HTTPException, ValueError) as exc:
        raise RuntimeError(f"Could not confirm Telegram logOut ({type(exc).__name__})") from exc
    finally:
        connection.close()


def wait_until_ready(process, ready, timeout, stopping, label):
    deadline = time.monotonic() + timeout
    while not stopping():
        if process.poll() is not None:
            raise RuntimeError(f"{label} exited during startup (code {process.returncode})")
        if ready():
            return True
        if time.monotonic() >= deadline:
            raise RuntimeError(f"{label} did not become ready within {timeout} seconds")
        time.sleep(0.2)
    return False


def wait_for_exit(process, timeout):
    if process is not None:
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            return False
    return True


def stop_child(process, timeout, *, console_break=False):
    if process is None or process.poll() is not None:
        return
    try:
        if os.name == "nt" and console_break:
            process.send_signal(signal.CTRL_BREAK_EVENT)
        else:
            process.terminate()
    except OSError:
        process.terminate()
    if not wait_for_exit(process, timeout):
        process.kill()
        process.wait()


def run(config, settings, data_dir, ready_file, stop_file, *, logout=False):
    stop_requested = False

    def request_stop(_signum, _frame):
        nonlocal stop_requested
        stop_requested = True

    def stopping():
        return stop_requested or (stop_file is not None and stop_file.exists())

    for name in ("SIGINT", "SIGTERM", "SIGBREAK"):
        shutdown_signal = getattr(signal, name, None)
        if shutdown_signal is not None:
            signal.signal(shutdown_signal, request_stop)

    process_group = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
    api = bot = None
    marker_created = False
    with tempfile.TemporaryDirectory(prefix="telegram-bot-runtime-") as control_dir:
        bot_ready = Path(control_dir) / "ready"
        bot_stop = Path(control_dir) / "stop"
        try:
            if stopping():
                return 0
            env = {
                **os.environ,
                "TSMD_BOT_READY_FILE": str(bot_ready),
                "TSMD_BOT_STOP_FILE": str(bot_stop),
            }
            env.pop("TSMD_LOCAL_ENDPOINT", None)
            env.pop("TELEGRAM_API_ID", None)
            env.pop("TELEGRAM_API_HASH", None)
            if settings.mode == "local":
                check_port_available(settings.port)
                command, server_env = local_server_options(config, settings, data_dir, prepare=True)
                api = subprocess.Popen(command, cwd=data_dir, env=server_env, **process_group)
                if not wait_until_ready(
                    api, lambda: server_is_ready(settings.port), settings.startup_timeout,
                    stopping, "Local Telegram Bot API",
                ):
                    return 0
                env["TSMD_LOCAL_ENDPOINT"] = settings.local_endpoint
                no_proxy = ",".join(filter(None, (env.get("no_proxy", env.get("NO_PROXY")), LOOPBACK)))
                env["NO_PROXY"] = env["no_proxy"] = no_proxy
            if logout:
                logout_bot(config["config"]["token"], settings)
                print(f"Telegram bot logged out from {settings.mode} Bot API.", flush=True)
                return 0
            bot = subprocess.Popen([sys.executable, "-u", str(ROOT / "bot.py")], cwd=data_dir, env=env, **process_group)
            if not wait_until_ready(bot, bot_ready.is_file, settings.startup_timeout, stopping, "Telegram bot"):
                return 0
            if api is not None and api.poll() is not None:
                raise RuntimeError("Local Telegram Bot API exited during bot startup")
            if ready_file is not None:
                ready_file.touch(exist_ok=False)
                marker_created = True
            print(f"Telegram bot ready ({settings.mode} mode).", flush=True)
            while not stopping():
                if bot.poll() is not None:
                    raise RuntimeError(f"Telegram bot exited unexpectedly (code {bot.returncode})")
                if api is not None and api.poll() is not None:
                    raise RuntimeError(f"Local Telegram Bot API exited unexpectedly (code {api.returncode})")
                time.sleep(0.2)
            return 0
        finally:
            try:
                if marker_created:
                    ready_file.unlink(missing_ok=True)
            finally:
                try:
                    if bot is not None and bot.poll() is None:
                        bot_stop.touch()
                        if not wait_for_exit(bot, settings.shutdown_timeout):
                            stop_child(bot, 5)
                finally:
                    stop_child(api, 15, console_break=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=ROOT, help="runtime directory containing config.txt")
    parser.add_argument("--ready-file", type=Path, help="create this marker after the bot is ready")
    parser.add_argument("--stop-file", type=Path, help="stop when the operator creates this marker")
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--check", action="store_true", help="validate setup without starting either process")
    action.add_argument("--log-out", action="store_true", help="log out of the selected API after stopping the running bot")
    args = parser.parse_args()

    try:
        data_dir = args.data_dir.resolve(strict=True)
        ready_file = args.ready_file.resolve() if args.ready_file else None
        stop_file = args.stop_file.resolve() if args.stop_file else None
        if ready_file is not None and (ready_file.exists() or ready_file == stop_file):
            raise ValueError("Use a new readiness marker distinct from the stop marker")
        if args.log_out and (ready_file is not None or stop_file is not None):
            raise ValueError("Do not use process markers with --log-out")
        config = read_config(data_dir / "config.txt")
        if not re.fullmatch(r"[0-9]+:[A-Za-z0-9_-]+", config.get("config", "token", fallback="")):
            raise ValueError("Configure a valid bot token in config.txt")
        settings = RuntimeSettings.from_config(config)
        MediaPolicy.from_config(config)
        if args.check:
            if settings.mode == "local":
                check_port_available(settings.port)
                local_server_options(config, settings, data_dir)
            print(f"Configuration OK ({settings.mode} mode).")
            return 0
        return run(config, settings, data_dir, ready_file, stop_file, logout=args.log_out)
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"Startup failed: {exc}", file=sys.stderr)
        return 1
    except configparser.Error:
        print("Startup failed: invalid config.txt syntax or values.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
