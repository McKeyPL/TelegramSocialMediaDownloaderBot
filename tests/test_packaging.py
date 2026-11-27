import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile

from scripts.package_release import MANIFEST_NAME, PACKAGE_NAME, build_release


class PackagingTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.repo = Path(self.temp_dir.name) / "source"
        self.repo.mkdir()
        self.output = Path(self.temp_dir.name) / "output"
        self.git("init", "--quiet")
        self.write("bot.py", b"# committed source\n")
        self.write("run_bot.py", b"# committed launcher\n")
        self.write("requirements.txt", b"")
        self.write("config.default.txt", b"[telegram]\n")

    def git(self, *args):
        return subprocess.run(
            ["git", "-c", "core.autocrlf=false", *args], cwd=self.repo, check=True, capture_output=True,
        ).stdout.decode().strip()

    def write(self, name, contents):
        path = self.repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(contents)

    def commit(self):
        self.git("add", "--all")
        self.git(
            "-c", "user.name=Packaging Test", "-c", "user.email=package@example.invalid",
            "-c", "commit.gpgsign=false", "commit", "--quiet", "-m", "Packaging fixture",
        )
        return self.git("rev-parse", "HEAD")

    def test_packages_only_approved_files_from_requested_commit(self):
        for filename in ("config.txt", "ig_session.json", ".env", ".venv/extra.py",
                         "handlers/__pycache__/extra.py", "logs/bot.log", "temp/clip.mp4",
                         "bot_api_data/state", "scripts/test_credentials.py"):
            self.write(filename, b"excluded fixture")
        self.write("handlers/media.py", b"# committed handler\n")
        self.write("scripts/deploy_windows.py", b"# committed deployment helper\n")
        sha = self.commit()
        self.write("bot.py", b"# modified source\n")
        self.write("untracked.py", b"# untracked code\n")
        self.write("handlers/media.py", b"# newer handler\n")
        self.git("add", "handlers/media.py")
        self.git("-c", "user.name=Packaging Test", "-c", "user.email=package@example.invalid",
                 "-c", "commit.gpgsign=false", "commit", "--quiet", "-m", "Newer fixture")

        archive_path = build_release(self.repo, sha, self.output)
        self.assertEqual(archive_path.name, f"{PACKAGE_NAME}-{sha}.zip")
        with ZipFile(archive_path) as archive:
            self.assertEqual(set(archive.namelist()), {
                "bot.py", "run_bot.py", "requirements.txt", "config.default.txt",
                "handlers/media.py", "scripts/deploy_windows.py", MANIFEST_NAME,
            })
            self.assertEqual(archive.read("bot.py"), b"# committed source\n")
            self.assertEqual(archive.read("handlers/media.py"), b"# committed handler\n")
            manifest = json.loads(archive.read(MANIFEST_NAME))
            self.assertEqual(manifest["schema_version"], 1)
            self.assertEqual(manifest["commit"], sha)
            self.assertEqual(manifest["files"], {
                name: hashlib.sha256(archive.read(name)).hexdigest()
                for name in archive.namelist() if name != MANIFEST_NAME
            })
        checksum, filename = archive_path.with_suffix(".zip.sha256").read_text().split()
        self.assertEqual(hashlib.sha256(archive_path.read_bytes()).hexdigest(), checksum)
        self.assertEqual(filename, archive_path.name)

    def test_fails_if_launcher_is_missing_in_commit(self):
        (self.repo / "run_bot.py").unlink()
        sha = self.commit()
        self.write("run_bot.py", b"# untracked launcher\n")

        with self.assertRaisesRegex(ValueError, "missing release files: run_bot.py"):
            build_release(self.repo, sha, self.output)

        self.assertFalse(self.output.exists())

    def test_same_revision_produces_same_archive_for_this_runtime(self):
        sha = self.commit()
        first = build_release(self.repo, sha, self.output)
        second = build_release(self.repo, sha, self.output / "second")
        self.assertEqual(first.read_bytes(), second.read_bytes())

    def test_packages_native_server_with_target_and_provenance(self):
        sha = self.commit()
        native = Path(self.temp_dir.name) / "native"
        native.joinpath("bin").mkdir(parents=True)
        native.joinpath("licenses").mkdir()
        native.joinpath("bin", "telegram-bot-api.exe").write_bytes(b"fixture executable")
        for component in ("Telegram-Bot-API", "TDLib", "OpenSSL", "zlib"):
            native.joinpath("licenses", f"{component}.txt").write_text("Example license", encoding="utf-8")
        native.joinpath("config.txt").write_bytes(b"excluded fixture")
        bot_api_sha = "a" * 40

        archive_path = build_release(
            self.repo, sha, self.output,
            native_dir=native, native_target="windows-x64", bot_api_commit=bot_api_sha,
        )

        self.assertEqual(archive_path.name, f"{PACKAGE_NAME}-{sha}-windows-x64.zip")
        with ZipFile(archive_path) as archive:
            self.assertNotIn("config.txt", archive.namelist())
            self.assertEqual(archive.read("bin/telegram-bot-api.exe"), b"fixture executable")
            self.assertEqual(archive.getinfo("bin/telegram-bot-api.exe").external_attr >> 16 & 0o777, 0o755)
            manifest = json.loads(archive.read(MANIFEST_NAME))
            self.assertEqual(manifest["telegram_bot_api"], {"target": "windows-x64", "commit": bot_api_sha})
            self.assertEqual(manifest["files"]["bin/telegram-bot-api.exe"],
                             hashlib.sha256(b"fixture executable").hexdigest())

    def test_native_package_fails_when_binary_is_missing(self):
        sha = self.commit()
        with self.assertRaisesRegex(ValueError, "Missing or invalid native bundle file: bin/telegram-bot-api"):
            build_release(
                self.repo, sha, self.output,
                native_dir=Path(self.temp_dir.name), native_target="linux-x64", bot_api_commit="a" * 40,
            )
        self.assertFalse(self.output.exists())
