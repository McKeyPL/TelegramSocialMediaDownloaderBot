"""Install the matching native Bot API from the project's public releases."""

import hashlib
import json
import platform
import re
import shutil
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.error import URLError
from urllib.request import urlopen
from zipfile import BadZipFile, ZipFile


RELEASES = "https://github.com/McKeyPL/TelegramSocialMediaDownloaderBot/releases/download"
MAX_BYTES = 200_000_000


def native_target():
    systems = {"Windows": "windows", "Linux": "linux", "Darwin": "macos"}
    system = systems.get(platform.system())
    architecture = {"amd64": "x64", "x86_64": "x64", "arm64": "arm64", "aarch64": "arm64"}.get(
        platform.machine().lower(),
    )
    if not system or not architecture or (system == "windows" and architecture != "x64"):
        raise ValueError("No automatic Bot API package is available for this platform.")
    return f"{system}-{architecture}"


def checkout_commit(root: Path) -> str:
    try:
        result = subprocess.check_output(
            ["git", "rev-parse", "--verify", "HEAD"], cwd=root, text=True,
            stderr=subprocess.DEVNULL, timeout=5,
        ).strip()
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError("Use the native release ZIP or run from a Git checkout.") from exc
    if not re.fullmatch(r"[0-9a-f]{40}", result):
        raise ValueError("Invalid Git commit for the native release.")
    return result


def download(url: str, path: Path, limit: int) -> str:
    digest = hashlib.sha256()
    total = 0
    with urlopen(url, timeout=30) as response, path.open("xb") as output:
        while chunk := response.read(256 * 1024):
            total += len(chunk)
            if total > limit:
                raise ValueError("GitHub release asset exceeds the download limit.")
            digest.update(chunk)
            output.write(chunk)
    return digest.hexdigest()


def install_native_api(root: Path, *, allow_download=True) -> Path:
    commit = checkout_commit(root)
    target = native_target()
    binary_name = "telegram-bot-api.exe" if target.startswith("windows-") else "telegram-bot-api"
    binary_member = f"bin/{binary_name}"
    directory = root / "tools" / "bot-api" / commit / target
    binary = directory / binary_name
    if binary.is_file():
        return binary
    if not allow_download:
        raise ValueError("The matching Bot API has not been downloaded yet. Start the bot to fetch it.")

    name = f"telegram-social-media-downloader-bot-{commit}-{target}.zip"
    url = f"{RELEASES}/{target}-beta-{commit}/{name}"
    directory.mkdir(parents=True, exist_ok=True)
    print(f"Fetching Bot API for {target} at {commit[:12]} from GitHub Releases.", flush=True)
    try:
        with TemporaryDirectory(dir=directory) as folder:
            staging = Path(folder)
            checksum = staging / "checksum"
            archive = staging / "release.zip"
            download(url + ".sha256", checksum, 1024)
            expected = checksum.read_text(encoding="ascii").split()
            if len(expected) != 2 or expected[1] != name or not re.fullmatch("[a-f0-9]{64}", expected[0]):
                raise ValueError("Invalid checksum for the native package.")
            if download(url, archive, MAX_BYTES) != expected[0]:
                raise ValueError("Native package checksum did not match.")

            staged_binary = staging / binary.name
            with ZipFile(archive) as package:
                manifest_entry = package.getinfo("release-manifest.json")
                if manifest_entry.file_size > 64 * 1024:
                    raise ValueError("Native package manifest is too large.")
                manifest = json.loads(package.read(manifest_entry))
                native = manifest.get("telegram_bot_api", {})
                if manifest.get("schema_version") != 1 or manifest.get("commit") != commit or native.get("target") != target:
                    raise ValueError("Native package is for a different revision or platform.")
                entry = package.getinfo(binary_member)
                if not 0 < entry.file_size <= MAX_BYTES:
                    raise ValueError("Invalid Bot API binary size.")
                with package.open(entry) as source, staged_binary.open("xb") as output:
                    shutil.copyfileobj(source, output)
                if hashlib.sha256(staged_binary.read_bytes()).hexdigest() != manifest["files"][binary_member]:
                    raise ValueError("Bot API binary checksum did not match.")

            staged_binary.chmod(0o755)
            subprocess.run([str(staged_binary), "--version"], check=True, timeout=15)
            try:
                # On Windows rename refuses to overwrite a concurrent install.
                staged_binary.rename(binary)
            except FileExistsError:
                pass
    except URLError as exc:
        raise ValueError(f"Package for {target} at {commit[:12]} is unavailable. Check the beta build on GitHub.") from exc
    except (BadZipFile, KeyError, TypeError, AttributeError, subprocess.SubprocessError) as exc:
        raise ValueError("Could not verify the published Bot API package.") from exc
    return binary
