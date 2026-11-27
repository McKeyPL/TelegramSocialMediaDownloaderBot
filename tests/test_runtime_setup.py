import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from zipfile import ZipFile

from scripts import sync_requirements, update_media_tools


class RuntimeSetupTests(unittest.TestCase):
    def test_sync_restores_pip_even_with_current_requirements_marker(self):
        requirements = Path(sync_requirements.__file__).resolve().parents[1] / "requirements.txt"
        digest = hashlib.sha256(requirements.read_bytes()).hexdigest()
        with tempfile.TemporaryDirectory() as folder:
            marker = Path(folder) / ".telegram-downloader-requirements"
            marker.write_text(digest, encoding="ascii")
            with (
                patch.object(sync_requirements.sys, "prefix", folder),
                patch.object(sync_requirements.subprocess, "run", side_effect=[
                    SimpleNamespace(returncode=1), None, None, None,
                ]) as run,
            ):
                sync_requirements.main()
        self.assertEqual([call.args[0][2] for call in run.call_args_list], ["pip", "ensurepip", "pip", "pip"])

    def test_ytdlp_upgrade_includes_ejs_dependencies(self):
        with patch.object(update_media_tools.subprocess, "run") as run:
            update_media_tools.update_ytdlp()
        self.assertIn("yt-dlp[default]", run.call_args_list[0].args[0])
        self.assertIn("--upgrade", run.call_args_list[0].args[0])

    def test_deno_verifies_checksum_and_reuses_current_version(self):
        archive = io.BytesIO()
        with ZipFile(archive, "w") as package:
            package.writestr("deno.exe", b"fixture")
        asset = archive.getvalue()
        metadata = json.dumps({"tag_name": "v2.3.0"}).encode()
        checksum = hashlib.sha256(asset).hexdigest().encode()
        with tempfile.TemporaryDirectory() as folder:
            with (
                patch.object(update_media_tools, "TOOLS", Path(folder)),
                patch.object(update_media_tools.platform, "machine", return_value="AMD64"),
                patch.object(update_media_tools, "urlopen", side_effect=[
                    io.BytesIO(metadata), io.BytesIO(checksum), io.BytesIO(asset), io.BytesIO(metadata),
                ]) as download,
                patch.object(update_media_tools.subprocess, "run", return_value=SimpleNamespace(
                    returncode=0, stdout="deno 2.3.0 test\n",
                )),
            ):
                update_media_tools.update_deno()
                self.assertEqual((Path(folder) / "deno.exe").read_bytes(), b"fixture")
                update_media_tools.update_deno()
            self.assertEqual(download.call_count, 4)
