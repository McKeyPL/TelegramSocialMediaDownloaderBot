"""Install and switch versions of the bot under a dedicated Windows directory."""

import argparse
import ctypes
import hashlib
import json
import os
from contextlib import contextmanager
from pathlib import Path, PureWindowsPath
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from uuid import uuid4
from zipfile import BadZipFile, ZipFile


MANIFEST = "release-manifest.json"
REQUIRED = {"run_bot.py", "bot.py", "requirements.txt", "config.default.txt"}
SHA256 = re.compile(r"[a-f0-9]{64}")
COMMIT = re.compile(r"[a-f0-9]{40}")
INSTANCE_ID = re.compile(r"[a-f0-9]{32}")
DEFAULT_READY_TIMEOUT = 180
DEFAULT_STOP_TIMEOUT = 120


class DeploymentError(RuntimeError):
    pass


def file_digest(file):
    digest = hashlib.sha256()
    for block in iter(lambda: file.read(1024 * 1024), b""):
        digest.update(block)
    return digest.hexdigest()


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path, value):
    temporary = path.with_name(f".{path.name}.{uuid4().hex}")
    try:
        temporary.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def safe_member(name):
    return all(
        component
        and not re.search(r'[\x00-\x1f\\:*?"<>|]', component)
        and not component.endswith((".", " "))
        and not PureWindowsPath(component).is_reserved()
        and component.casefold() not in {
            ".env", ".git", ".prepared.json", ".venv", "__pycache__", "config.txt", "ig_session.json",
        }
        for component in name.split("/")
    )


def extract_release(archive, expected_digest, destination):
    if not SHA256.fullmatch(expected_digest):
        raise DeploymentError("The expected ZIP SHA-256 must contain 64 hexadecimal characters.")
    with archive.open("rb") as stream:
        if file_digest(stream) != expected_digest:
            raise DeploymentError("The ZIP SHA-256 does not match the expected digest.")
        stream.seek(0)
        with ZipFile(stream) as zip_file:
            members = zip_file.infolist()
            names = [member.filename for member in members]
            if (
                len({name.casefold() for name in names}) != len(names)
                or any(not safe_member(name) for name in names)
                or any(member.is_dir() or stat.S_ISLNK(member.external_attr >> 16) for member in members)
            ):
                raise DeploymentError("The ZIP contains an invalid, duplicate, or unsafe path.")
            if MANIFEST not in names:
                raise DeploymentError("The ZIP has no release manifest.")

            manifest = json.loads(zip_file.read(MANIFEST))
            if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
                raise DeploymentError("The release manifest schema is unsupported.")
            commit = manifest.get("commit")
            files = manifest.get("files")
            if not isinstance(commit, str) or not COMMIT.fullmatch(commit):
                raise DeploymentError("The manifest has no full Git commit SHA.")
            if not isinstance(files, dict) or set(files) != set(names) - {MANIFEST} or not REQUIRED <= set(files):
                raise DeploymentError("The manifest does not describe the required package files.")

            for name, expected in files.items():
                if not isinstance(expected, str) or not SHA256.fullmatch(expected):
                    raise DeploymentError("The manifest contains an invalid file digest.")
                path = destination / name
                path.parent.mkdir(parents=True, exist_ok=True)
                with zip_file.open(name) as source, path.open("xb") as target:
                    shutil.copyfileobj(source, target)
                with path.open("rb") as target:
                    if file_digest(target) != expected:
                        raise DeploymentError(f"Package file digest mismatch: {name}")
            write_json(destination / MANIFEST, manifest)
    return commit


@contextmanager
def deployment_lock(path):
    with path.open("a+b") as file:
        file.seek(0)
        if os.name == "nt":
            import msvcrt

            # Windows permits locking a byte beyond EOF.
            try:
                msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise DeploymentError("Another deployment operation is in progress.") from exc
        else:
            import fcntl

            try:
                fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise DeploymentError("Another deployment operation is in progress.") from exc
        yield


def windows_process_identity(pid):
    """Return the process creation time, or None if this PID has exited."""
    from ctypes import wintypes

    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    api.OpenProcess.restype = wintypes.HANDLE
    api.GetProcessTimes.argtypes = (wintypes.HANDLE,) + (ctypes.POINTER(wintypes.FILETIME),) * 4
    api.GetProcessTimes.restype = wintypes.BOOL
    api.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    api.WaitForSingleObject.restype = wintypes.DWORD
    api.CloseHandle.argtypes = (wintypes.HANDLE,)
    api.CloseHandle.restype = wintypes.BOOL

    handle = api.OpenProcess(0x101000, False, pid)  # SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        error = ctypes.get_last_error()
        if error == 87:  # ERROR_INVALID_PARAMETER, including a PID that has exited.
            return None
        raise ctypes.WinError(error)
    try:
        status = api.WaitForSingleObject(handle, 0)
        if status == 0:  # WAIT_OBJECT_0
            return None
        if status != 258:  # WAIT_TIMEOUT
            raise ctypes.WinError(ctypes.get_last_error())
        created, exited, kernel, user = [wintypes.FILETIME() for _ in range(4)]
        if not api.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited), ctypes.byref(kernel), ctypes.byref(user)):
            raise ctypes.WinError(ctypes.get_last_error())
        return (created.dwHighDateTime << 32) | created.dwLowDateTime
    finally:
        api.CloseHandle(handle)


def wait_until(predicate, timeout):
    deadline = time.monotonic() + timeout
    while not predicate():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(remaining, 0.1))
    return True


def release_python(release):
    return release / ".venv" / "Scripts" / "python.exe"


def install_dependencies(release):
    subprocess.run([sys.executable, "-m", "venv", ".venv"], cwd=release, check=True)
    python = release_python(release)
    subprocess.run(
        [python, "-m", "pip", "install", "--disable-pip-version-check", "--no-input", "-r", "requirements.txt"],
        cwd=release, check=True,
    )
    subprocess.run([python, "-m", "pip", "check"], cwd=release, check=True)
    subprocess.run([python, "-m", "py_compile", "run_bot.py", "bot.py"], cwd=release, check=True)


class Deployment:
    def __init__(self, root, *, ready_timeout=DEFAULT_READY_TIMEOUT, stop_timeout=DEFAULT_STOP_TIMEOUT):
        self.root = root.resolve()
        self.state_path = self.root / "state.json"
        self.releases = self.root / "releases"
        self.data = self.root / "data"
        self.instances = self.root / "instances"
        self.ready_timeout = ready_timeout
        self.stop_timeout = stop_timeout
        self.state = read_json(self.state_path) if self.state_path.exists() else {}
        self.started_process = None

    def save(self, **changes):
        updated = self.state | changes
        write_json(self.state_path, updated)
        self.state = updated

    def release_path(self, commit):
        if not isinstance(commit, str) or not COMMIT.fullmatch(commit):
            raise DeploymentError("No valid release has been selected.")
        return self.releases / commit

    def prepared_release(self, commit):
        release = self.release_path(commit)
        if (
            release.resolve().parent != self.releases
            or not (release / ".prepared.json").is_file()
            or not release_python(release).is_file()
        ):
            raise DeploymentError(f"Release {commit} is missing or incomplete.")
        return release

    def prepare_release(self, archive, digest):
        self.releases.mkdir(parents=True, exist_ok=True)
        if self.releases.resolve().parent != self.root:
            raise DeploymentError("The releases directory must stay inside the deployment root.")
        with tempfile.TemporaryDirectory(prefix=".extract-", dir=self.releases) as scratch:
            staging = Path(scratch) / "package"
            staging.mkdir()
            commit = extract_release(archive, digest, staging)
            release = self.release_path(commit)
            if release.exists():
                if (release / ".prepared.json").is_file():
                    if read_json(release / ".prepared.json").get("digest") != digest:
                        raise DeploymentError("The installed commit was prepared from a different ZIP digest.")
                    self.prepared_release(commit)
                    for name, expected in read_json(staging / MANIFEST)["files"].items():
                        with (release / name).open("rb") as installed:
                            if file_digest(installed) != expected:
                                raise DeploymentError(f"Installed package file digest mismatch: {name}")
                    return commit
                release.rename(release.with_name(f"{commit}.incomplete-{uuid4().hex}"))
            staging.rename(release)
        try:
            install_dependencies(release)
        except (OSError, subprocess.CalledProcessError) as exc:
            raise DeploymentError(f"Dependency setup failed for {commit}. The current instance was left running.") from exc
        write_json(release / ".prepared.json", {"digest": digest})
        return commit

    def active_instance(self):
        instance = self.state.get("instance")
        if not instance:
            return None
        if not INSTANCE_ID.fullmatch(instance["run_id"]):
            raise DeploymentError("The recorded instance ID is invalid.")
        pid, identity = instance.get("pid"), instance.get("identity")
        if pid is None or identity is None:
            # A prior controller may have stopped after spawning the launcher.
            # Refuse to start a second instance blindly.
            directory = self.instances / instance["run_id"]
            raise DeploymentError(f"Launcher identity was not recorded. Check the instance under {directory}.")
        if windows_process_identity(pid) != identity:
            return None
        return instance

    def ensure_config(self):
        if not (self.data / "config.txt").is_file():
            raise DeploymentError(f"Set up {self.data / 'config.txt'} before starting a release.")

    def start_release(self, commit):
        self.ensure_config()
        release = self.prepared_release(commit)
        if self.active_instance():
            raise DeploymentError("A managed instance is already running.")

        run_id = uuid4().hex
        directory = self.instances / run_id
        directory.mkdir(parents=True)
        stop_file = directory / "stop"
        ready_file = directory / "ready"
        instance = {"run_id": run_id, "commit": commit}
        self.save(instance=instance)

        command = [
            release_python(release), release / "run_bot.py", "--data-dir", self.data,
            "--ready-file", ready_file, "--stop-file", stop_file,
        ]
        try:
            with (directory / "process.log").open("ab") as log:
                process = subprocess.Popen(
                    command, cwd=self.data, stdin=subprocess.DEVNULL, stdout=log,
                    stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NO_WINDOW,
                    env=dict(os.environ, PYTHONUNBUFFERED="1"),
                )
        except OSError:
            self.save(instance=None)
            raise

        try:
            identity = windows_process_identity(process.pid)
            if identity is None:
                process.wait(timeout=1)
                raise DeploymentError(f"Launcher exited during startup. See {directory / 'process.log'}.")
            self.save(instance=instance | {"pid": process.pid, "identity": identity})
            wait_until(lambda: ready_file.is_file() or process.poll() is not None, self.ready_timeout)
            if not ready_file.is_file() or process.poll() is not None:
                raise DeploymentError(f"Launcher did not become ready. See {directory / 'process.log'}.")
            self.started_process = process
        except (DeploymentError, OSError, KeyboardInterrupt, subprocess.TimeoutExpired):
            stop_file.touch()
            try:
                process.wait(timeout=self.stop_timeout)
            except subprocess.TimeoutExpired as exc:
                raise DeploymentError("The new launcher did not stop. Resolve it before retrying.") from exc
            self.save(instance=None)
            raise

    def stop(self):
        instance = self.state.get("instance")
        if instance and INSTANCE_ID.fullmatch(instance["run_id"]):
            (self.instances / instance["run_id"] / "stop").touch()
        if self.active_instance() and not wait_until(lambda: self.active_instance() is None, self.stop_timeout):
            raise DeploymentError("The launcher did not stop within the timeout. No other instance was started.")
        if self.started_process and self.started_process.poll() is not None:
            self.started_process = None
        self.save(instance=None)

    def activate(self, target):
        self.ensure_config()
        self.prepared_release(target)
        previous = self.state.get("current")
        running = self.active_instance()
        if running and running["commit"] == target and target == previous:
            return
        if running and running["commit"] != previous:
            raise DeploymentError("Running instance does not match the current release. Stop it first.")
        self.stop()
        try:
            self.start_release(target)
            if target != previous:
                self.save(current=target, previous=previous)
        except (DeploymentError, OSError, KeyboardInterrupt) as exc:
            try:
                self.stop()
                if running:
                    self.start_release(previous)
            except (DeploymentError, OSError, KeyboardInterrupt) as rollback_error:
                raise DeploymentError(f"Upgrade failed: {exc}\nRecovery also failed: {rollback_error}") from rollback_error
            if running:
                raise DeploymentError(f"Upgrade failed: {exc}\nPrevious release {previous} restarted.") from exc
            raise

    def status(self):
        try:
            running = self.active_instance()
            return {"current": self.state.get("current"), "previous": self.state.get("previous"), "running": running}
        except DeploymentError as exc:
            return {"current": self.state.get("current"), "previous": self.state.get("previous"), "error": str(exc)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True, help="Dedicated directory for releases, data, and state")
    parser.add_argument(
        "--ready-timeout", type=int, default=DEFAULT_READY_TIMEOUT,
        help="Total startup wait in seconds. Allow time for both the local API and the bot.",
    )
    parser.add_argument(
        "--stop-timeout", type=int, default=DEFAULT_STOP_TIMEOUT,
        help="Graceful stop wait in seconds. Allow time for the bot and local API to exit.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    deploy = commands.add_parser("deploy", help="Verify, install and start a release package")
    deploy.add_argument("archive", type=Path)
    deploy.add_argument("--sha256", required=True, help="Expected ZIP SHA-256 from the trusted release")
    for command in ("start", "stop", "rollback", "status"):
        commands.add_parser(command)
    args = parser.parse_args(argv)
    if os.name != "nt":
        parser.error("Use this deployment command on Windows.")
    if args.ready_timeout <= 0 or args.stop_timeout <= 0:
        parser.error("Timeouts must be greater than zero.")

    try:
        root = args.root.resolve()
        root.mkdir(parents=True, exist_ok=True)
        with deployment_lock(root / "deploy.lock"):
            deployment = Deployment(root, ready_timeout=args.ready_timeout, stop_timeout=args.stop_timeout)
            if args.command == "deploy":
                deployment.ensure_config()
                commit = deployment.prepare_release(args.archive, args.sha256.lower())
                deployment.activate(commit)
            elif args.command == "rollback":
                deployment.activate(deployment.state.get("previous"))
            elif args.command == "start":
                deployment.activate(deployment.state.get("current"))
            elif args.command == "stop":
                deployment.stop()
            print(json.dumps(deployment.status(), indent=2))
    except (DeploymentError, OSError, BadZipFile, ValueError, subprocess.SubprocessError) as exc:
        print(f"Deployment failed: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
