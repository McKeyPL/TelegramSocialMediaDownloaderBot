import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
# Stand-in for the native binary. The launched bot uses its real Telegram client.
FIXTURE_API = """
import json
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit


class API(BaseHTTPRequestHandler):
    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/":
            status, response = 404, {"ok": False, "error_code": 404}
        elif path == "/bot424242:fixture123/getMe":
            status, response = 200, {"ok": True, "result": {
                "id": 424242, "is_bot": True, "first_name": "Fixture", "username": "fixturebot",
            }}
        elif path == "/bot424242:fixture123/getUpdates":
            Path("updates-checked").touch()
            time.sleep(0.1)
            status, response = 200, {"ok": True, "result": []}
        else:
            status, response = 404, {"ok": False, "error_code": 404}
        body = json.dumps(response).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_POST = do_GET

    def log_message(self, *_args):
        pass


with ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), API) as server:
    print(f"Fixture Bot API listening on {server.server_port}", flush=True)
    server.serve_forever()
"""

FIXTURE_LAUNCHER = """
import os
import sys
import run_bot

fixture_api = sys.argv.pop(1)
run_bot.local_server_options = lambda config, settings, *args, **kwargs: (
    [sys.executable, "-u", fixture_api, str(settings.port)], os.environ.copy(),
)
raise SystemExit(run_bot.main())
"""


class LauncherProcessTests(unittest.TestCase):
    def test_starts_bot_polling_and_stops_both_processes(self):
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]

        with tempfile.TemporaryDirectory(prefix="bot process test ") as folder:
            data = Path(folder)
            api = data / "api fixture.py"
            api.write_text(FIXTURE_API, encoding="utf-8")
            (data / "config.txt").write_text(
                "[config]\ntoken = 424242:fixture123\nallowed_users = []\nallowed_chats = []\n"
                "[telegram]\nmode = local\n"
                f"local_port = {port}\nstartup_timeout = 40\nshutdown_timeout = 8\n"
                "[instagram]\nenabled = no\n[9gag]\nuse_selenium = no\n[youtube]\nenabled = no\n",
                encoding="utf-8",
            )
            ready, stop = data / "ready", data / "stop"
            env = {key: value for key, value in os.environ.items() if not key.startswith("TSMD_")}
            with (data / "test.log").open("w+", encoding="utf-8") as log:
                with subprocess.Popen(
                    [sys.executable, "-u", "-c", FIXTURE_LAUNCHER, str(api),
                     "--data-dir", str(data), "--ready-file", str(ready), "--stop-file", str(stop)],
                    cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, env=env,
                ) as launcher:
                    try:
                        deadline = time.monotonic() + 90
                        while launcher.poll() is None and not ready.exists() and time.monotonic() < deadline:
                            time.sleep(0.1)
                        if not ready.exists():
                            log.seek(0)
                            self.fail(log.read())
                        self.assertTrue((data / "updates-checked").exists())
                    finally:
                        stop.touch()
                        try:
                            launcher.wait(timeout=30)
                        except subprocess.TimeoutExpired:
                            launcher.terminate()
                            raise
            self.assertEqual(launcher.returncode, 0)
            self.assertFalse(ready.exists())
            with socket.socket() as probe:
                self.assertNotEqual(probe.connect_ex(("127.0.0.1", port)), 0)
