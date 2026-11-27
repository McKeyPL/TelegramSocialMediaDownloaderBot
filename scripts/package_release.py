"""Build a deployable ZIP exclusively from the contents of a Git commit."""

import argparse
import hashlib
import json
import re
import stat
import subprocess
from pathlib import Path, PurePosixPath
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo


PACKAGE_NAME = "telegram-social-media-downloader-bot"
MANIFEST_NAME = "release-manifest.json"
REQUIRED_FILES = {"bot.py", "run_bot.py", "requirements.txt", "config.default.txt"}
NATIVE_TARGETS = {
    "windows-x64": "bin/telegram-bot-api.exe",
    "linux-x64": "bin/telegram-bot-api",
    "linux-arm64": "bin/telegram-bot-api",
    "macos-x64": "bin/telegram-bot-api",
    "macos-arm64": "bin/telegram-bot-api",
}
NATIVE_LICENSES = {"licenses/Telegram-Bot-API.txt", "licenses/TDLib.txt", "licenses/OpenSSL.txt"}
OPTIONAL_FILES = {
    "install_local.bat",
    "start_bot.bat",
    "start_bot_local.bat",
    "scripts/install_local.py",
    "scripts/build_bot_api_windows.ps1",
    "LICENSE",
    "README.md",
    "ROADMAP.md",
    "Dockerfile",
    ".dockerignore",
    "logs/.gitkeep",
    "scripts/deploy_windows.py",
    "scripts/install_requirements.ps1",
}


def is_release_file(name: str) -> bool:
    path = PurePosixPath(name)
    if name in REQUIRED_FILES | OPTIONAL_FILES:
        return True
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*\.py", name):
        return True
    if len(path.parts) == 2:
        folder, filename = path.parts
        return (
            folder == "handlers" and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*\.py", filename) is not None
            or folder == "docs" and re.fullmatch(r"[A-Za-z0-9_-]+\.md", filename) is not None
        )
    return False


def _git(repo: Path, *args: str) -> bytes:
    return subprocess.run(["git", *args], cwd=repo, check=True, stdout=subprocess.PIPE).stdout


def _write_member(archive: ZipFile, name: str, contents: bytes, mode: int = 0o644) -> None:
    entry = ZipInfo(name)
    entry.compress_type = ZIP_DEFLATED
    entry.external_attr = (stat.S_IFREG | mode) << 16
    archive.writestr(entry, contents)


def _native_files(directory: Path, target: str) -> dict[str, tuple[bytes, int]]:
    binary = NATIVE_TARGETS[target]
    licenses = set(NATIVE_LICENSES)
    if target.startswith(("linux-", "windows-")):
        licenses.add("licenses/zlib.txt")
    if target.startswith("linux-"):
        licenses.add("licenses/GCC.txt")
    files = {}
    for name in sorted(licenses | {binary}):
        source = directory / name
        if not source.is_file() or source.is_symlink() or not source.stat().st_size:
            raise ValueError(f"Missing or invalid native bundle file: {name}")
        files[name] = (source.read_bytes(), 0o755 if name == binary else 0o644)
    return files


def build_release(
    repo: Path,
    ref: str,
    output_dir: Path,
    *,
    native_dir: Path | None = None,
    native_target: str | None = None,
    bot_api_commit: str | None = None,
) -> Path:
    commit = _git(repo, "rev-parse", "--verify", "--end-of-options", f"{ref}^{{commit}}").decode().strip()
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("Expected a full SHA-1 Git commit ID")

    tree = _git(repo, "ls-tree", "-r", "-z", "--full-tree", commit)
    files: dict[str, tuple[bytes, int]] = {}
    windows_names = set()
    for entry in tree.rstrip(b"\0").split(b"\0"):
        if not entry:
            continue
        metadata, path = entry.split(b"\t", 1)
        name = path.decode("utf-8", errors="surrogateescape")
        if not is_release_file(name):
            continue
        mode, kind, oid = metadata.decode("ascii").split()
        if kind != "blob" or mode not in {"100644", "100755"}:
            raise ValueError(f"Release files must be regular files: {name}")
        if name.casefold() in windows_names:
            raise ValueError(f"Duplicate release path on Windows: {name}")
        # Read the blob directly so Git's checkout filters and line endings cannot change the release.
        files[name] = (_git(repo, "cat-file", "blob", oid), int(mode[-3:], 8))
        windows_names.add(name.casefold())

    missing = REQUIRED_FILES - files.keys()
    if missing:
        raise ValueError(f"Commit is missing release files: {', '.join(sorted(missing))}")

    manifest: dict = {
        "schema_version": 1,
        "commit": commit,
    }
    if any(value is not None for value in (native_dir, native_target, bot_api_commit)):
        if native_dir is None or native_target not in NATIVE_TARGETS or not re.fullmatch(r"[0-9a-f]{40}", bot_api_commit or ""):
            raise ValueError("Native packaging requires --native-dir, --native-target and a full --bot-api-commit")
        files.update(_native_files(native_dir, native_target))
        manifest["telegram_bot_api"] = {"target": native_target, "commit": bot_api_commit}
    manifest["files"] = {name: hashlib.sha256(files[name][0]).hexdigest() for name in sorted(files)}

    output_dir.mkdir(parents=True, exist_ok=True)
    target_suffix = f"-{native_target}" if native_target else ""
    archive_path = output_dir / f"{PACKAGE_NAME}-{commit}{target_suffix}.zip"
    with ZipFile(archive_path, "w") as archive:
        for name in sorted(files):
            _write_member(archive, name, *files[name])
        _write_member(archive, MANIFEST_NAME, (json.dumps(manifest, indent=2) + "\n").encode())

    archive_hash = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    archive_path.with_suffix(".zip.sha256").write_bytes(f"{archive_hash}  {archive_path.name}\n".encode("ascii"))
    return archive_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ref", default="HEAD", help="Git revision to package")
    parser.add_argument("--output-dir", type=Path, default=Path("dist"))
    parser.add_argument("--native-dir", type=Path, help="Staged native Bot API binary and license directory")
    parser.add_argument("--native-target", choices=sorted(NATIVE_TARGETS))
    parser.add_argument("--bot-api-commit", help="Exact Telegram Bot API source commit")
    args = parser.parse_args()
    try:
        archive_path = build_release(
            Path(__file__).resolve().parents[1], args.ref, args.output_dir,
            native_dir=args.native_dir, native_target=args.native_target, bot_api_commit=args.bot_api_commit,
        )
    except (OSError, subprocess.CalledProcessError, ValueError) as exc:
        parser.exit(1, f"Could not build release package: {exc}\n")
    print(archive_path)


if __name__ == "__main__":
    main()
