import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from scripts import deploy_windows


OLD = "1" * 40
NEW = "2" * 40
BROKEN = "3" * 40
FAKE_LAUNCHER = textwrap.dedent("""
    import argparse
    import time
    from pathlib import Path

    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--ready-file", type=Path, required=True)
    parser.add_argument("--stop-file", type=Path, required=True)
    arguments = parser.parse_args()
    release = Path(__file__).resolve().parent.name
    running = arguments.data_dir / "fake.pid"
    with running.open("x") as file:
        file.write(release)
    try:
        if not (arguments.data_dir / "config.txt").is_file():
            raise SystemExit(2)
        arguments.ready_file.touch()
        while not arguments.stop_file.is_file():
            time.sleep(0.02)
    finally:
        running.unlink()
""")


def write_release(path, commit, launcher=FAKE_LAUNCHER, extras=None):
    files = {
        "run_bot.py": launcher,
        "bot.py": "",
        "requirements.txt": "",
        "config.default.txt": "[config]\ntoken = example\n",
    }
    files.update(extras or {})
    hashes = {name: hashlib.sha256(value.encode()).hexdigest() for name, value in files.items()}
    with ZipFile(path, "w") as archive:
        for name, value in files.items():
            archive.writestr(name, value)
        archive.writestr(deploy_windows.MANIFEST, json.dumps({
            "schema_version": 1, "commit": commit, "files": hashes,
        }))
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enter_context(tempfile.TemporaryDirectory()))
        self.archive = self.root / "release.zip"
        self.staging = self.root / "extracted"
        self.staging.mkdir()

    # unittest.TestCase.enterContext arrived in Python 3.11.
    def enter_context(self, context):
        self.addCleanup(context.__exit__, None, None, None)
        return context.__enter__()

    def test_extracts_verified_release(self):
        digest = write_release(self.archive, OLD)
        self.assertEqual(deploy_windows.extract_release(self.archive, digest, self.staging), OLD)
        self.assertEqual((self.staging / "run_bot.py").read_text(), FAKE_LAUNCHER)

    def test_rejects_unexpected_zip_digest(self):
        write_release(self.archive, OLD)
        with self.assertRaisesRegex(deploy_windows.DeploymentError, "ZIP SHA-256 does not match"):
            deploy_windows.extract_release(self.archive, "0" * 64, self.staging)
        self.assertEqual(list(self.staging.iterdir()), [])

    def test_rejects_manifest_file_digest_mismatch(self):
        write_release(self.archive, OLD)
        with ZipFile(self.archive) as archive:
            files = {name: archive.read(name) for name in archive.namelist()}
        files["run_bot.py"] += b"\n"
        with ZipFile(self.archive, "w") as archive:
            for name, value in files.items():
                archive.writestr(name, value)
        digest = hashlib.sha256(self.archive.read_bytes()).hexdigest()
        with self.assertRaisesRegex(deploy_windows.DeploymentError, "file digest mismatch"):
            deploy_windows.extract_release(self.archive, digest, self.staging)

    def test_rejects_unsafe_and_case_colliding_names(self):
        for extras in (
            {"../escaped.py": ""},
            {"handlers/config.txt": ""},
            {"C:/absolute.py": ""},
            {"handlers/aux.txt": ""},
            {"config.DEFAULT.txt": ""},
        ):
            with self.subTest(extras=extras):
                digest = write_release(self.archive, OLD, extras=extras)
                with self.assertRaisesRegex(deploy_windows.DeploymentError, "unsafe path"):
                    deploy_windows.extract_release(self.archive, digest, self.staging)
        self.assertEqual(list(self.staging.iterdir()), [])

    def test_exclusive_lock_refuses_a_second_operation(self):
        lock = self.root / "deploy.lock"
        with deploy_windows.deployment_lock(lock):
            with self.assertRaisesRegex(deploy_windows.DeploymentError, "operation is in progress"):
                with deploy_windows.deployment_lock(lock):
                    self.fail("The second operation acquired an exclusive lock.")
        with deploy_windows.deployment_lock(lock):
            self.assertTrue(lock.exists())


@unittest.skipUnless(os.name == "nt", "Windows launcher process lifecycle")
class WindowsDeploymentTests(unittest.TestCase):
    def setUp(self):
        temp_dir = tempfile.TemporaryDirectory(prefix="bot staging ")
        self.addCleanup(temp_dir.cleanup)
        # The fixture uses the suite interpreter and has no external dependencies.
        installer = patch.object(deploy_windows, "install_dependencies")
        interpreter = patch.object(deploy_windows, "release_python", return_value=Path(sys.executable))
        self.addCleanup(installer.stop)
        self.addCleanup(interpreter.stop)
        self.install = installer.start()
        interpreter.start()
        self.root = Path(temp_dir.name)
        self.deployment = deploy_windows.Deployment(self.root, ready_timeout=2, stop_timeout=2)
        self.deployment.data.mkdir()
        self.config = self.deployment.data / "config.txt"
        self.config.write_bytes(b"fixture config")
        self.session = self.deployment.data / "ig_session.json"
        self.session.write_bytes(b"fixture session")
        self.tracker = self.deployment.data / "fake.pid"
        self.addCleanup(self.deployment.stop)

    def package(self, commit, launcher=FAKE_LAUNCHER):
        path = self.root / f"{commit}.zip"
        digest = write_release(path, commit, launcher)
        self.assertEqual(self.deployment.prepare_release(path, digest), commit)

    def assert_running(self, commit):
        self.assertEqual(self.tracker.read_text(), commit)
        self.assertEqual(self.deployment.status()["running"]["commit"], commit)
        self.assertEqual(self.config.read_bytes(), b"fixture config")
        self.assertEqual(self.session.read_bytes(), b"fixture session")

    def test_upgrade_stop_start_and_explicit_rollback_on_real_processes(self):
        self.package(OLD)
        self.deployment.activate(OLD)
        self.assert_running(OLD)

        self.package(NEW)
        self.assert_running(OLD)
        self.deployment.activate(NEW)
        self.assert_running(NEW)
        self.assertEqual(self.deployment.status()["previous"], OLD)

        self.deployment.activate(self.deployment.state["previous"])
        self.assert_running(OLD)
        self.deployment.stop()
        self.assertIsNone(self.deployment.status()["running"])
        self.assertFalse(self.tracker.exists())
        self.deployment.activate(self.deployment.state["current"])
        self.assert_running(OLD)

    def test_restarts_previous_release_if_new_start_fails(self):
        self.package(OLD)
        self.deployment.activate(OLD)
        self.package(BROKEN, "raise SystemExit(7)\n")
        with self.assertRaisesRegex(deploy_windows.DeploymentError, "Previous release .* restarted"):
            self.deployment.activate(BROKEN)
        self.assert_running(OLD)
        self.assertEqual(self.deployment.status()["current"], OLD)

    def test_unready_release_honors_stop_before_rollback(self):
        self.package(OLD)
        self.deployment.activate(OLD)
        self.package(BROKEN, FAKE_LAUNCHER.replace("    arguments.ready_file.touch()\n", ""))
        with self.assertRaisesRegex(deploy_windows.DeploymentError, "Previous release .* restarted"):
            self.deployment.activate(BROKEN)
        self.assert_running(OLD)

    def test_install_failure_leaves_running_release_in_place(self):
        self.package(OLD)
        self.deployment.activate(OLD)
        self.install.side_effect = subprocess.CalledProcessError(1, "pip install")
        with self.assertRaisesRegex(deploy_windows.DeploymentError, "current instance was left running"):
            self.package(BROKEN)
        self.assert_running(OLD)

    def test_reused_pid_does_not_count_as_managed_instance(self):
        with patch.object(deploy_windows, "windows_process_identity", return_value=123):
            self.deployment.save(instance={"run_id": "a" * 32, "pid": 42, "identity": 987, "commit": OLD})
            self.assertIsNone(self.deployment.active_instance())
            self.deployment.save(instance=None)
