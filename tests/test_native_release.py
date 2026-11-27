import hashlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

import native_release


class NativeReleaseTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.commit = "a" * 40
        self.target = "windows-x64"
        self.member = "bin/telegram-bot-api.exe"
        self.binary = b"fixture binary"

    def release_assets(self, *, valid_checksum=True, target="windows-x64"):
        stream = io.BytesIO()
        with ZipFile(stream, "w") as archive:
            archive.writestr(self.member, self.binary)
            archive.writestr("release-manifest.json", json.dumps({
                "schema_version": 1, "commit": self.commit,
                "telegram_bot_api": {"target": target},
                "files": {self.member: hashlib.sha256(self.binary).hexdigest()},
            }))
        contents = stream.getvalue()
        digest = hashlib.sha256(contents).hexdigest() if valid_checksum else "0" * 64
        name = f"telegram-social-media-downloader-bot-{self.commit}-{self.target}.zip"
        return [io.BytesIO(f"{digest}  {name}\n".encode()), io.BytesIO(contents)]

    def test_download_installs_once_and_check_reuses_cache(self):
        with (
            patch.object(native_release, "checkout_commit", return_value=self.commit),
            patch.object(native_release, "native_target", return_value=self.target),
            patch.object(native_release, "urlopen", side_effect=self.release_assets()) as request,
            patch.object(native_release.subprocess, "run") as version,
        ):
            with self.assertRaisesRegex(ValueError, "not been downloaded"):
                native_release.install_native_api(self.root, allow_download=False)
            request.assert_not_called()
            binary = native_release.install_native_api(self.root)
            self.assertEqual(binary.read_bytes(), self.binary)
            self.assertEqual(native_release.install_native_api(self.root, allow_download=False), binary)
            self.assertEqual(request.call_count, 2)
            version.assert_called_once()

    def test_bad_checksum_or_wrong_platform_never_installs(self):
        for options in ({"valid_checksum": False}, {"target": "macos-x64"}):
            with (
                self.subTest(options=options),
                patch.object(native_release, "checkout_commit", return_value=self.commit),
                patch.object(native_release, "native_target", return_value=self.target),
                patch.object(native_release, "urlopen", side_effect=self.release_assets(**options)),
                patch.object(native_release.subprocess, "run") as version,
            ):
                with self.assertRaises(ValueError):
                    native_release.install_native_api(self.root)
                version.assert_not_called()
                self.assertFalse(list(self.root.rglob("*.exe")))

    def test_unix_packages_use_native_platform_and_executable_permissions(self):
        for system, machine, target in (("Linux", "aarch64", "linux-arm64"), ("Darwin", "x86_64", "macos-x64")):
            with self.subTest(target=target):
                self.target = target
                self.member = "bin/telegram-bot-api"
                with (
                    patch.object(native_release, "checkout_commit", return_value=self.commit),
                    patch.object(native_release.platform, "system", return_value=system),
                    patch.object(native_release.platform, "machine", return_value=machine),
                    patch.object(native_release, "urlopen", side_effect=self.release_assets(target=target)) as request,
                    patch.object(native_release.subprocess, "run"),
                    patch.object(Path, "chmod", autospec=True, side_effect=Path.chmod) as chmod,
                ):
                    binary = native_release.install_native_api(self.root)
                self.assertEqual(binary.parent.name, target)
                self.assertEqual(binary.name, "telegram-bot-api")
                self.assertEqual(chmod.call_args.args[1], 0o755)
                if os.name != "nt":
                    self.assertTrue(binary.stat().st_mode & 0o100)
                self.assertIn(f"/{target}-beta-{self.commit}/", request.call_args.args[0])
