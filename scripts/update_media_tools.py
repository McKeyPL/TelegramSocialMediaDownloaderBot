"""Update the media tools used by the Windows bot."""
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.request import urlopen
from zipfile import BadZipFile, ZipFile


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools" / "media"
MAX_ARCHIVE = 150_000_000


def update_ytdlp():
    print("Checking yt-dlp and its JavaScript components for updates.", flush=True)
    try:
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "--no-input",
             "--timeout", "15", "--retries", "1", "--upgrade", "yt-dlp[default]"],
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        print("yt-dlp update failed; checking the existing installation.", file=sys.stderr)
    subprocess.run([sys.executable, "-m", "yt_dlp", "--version"], check=True)


def update_deno():
    binary = TOOLS / "deno.exe"
    with urlopen("https://api.github.com/repos/denoland/deno/releases/latest", timeout=15) as response:
        release = json.loads(response.read(2_000_000))
    tag = release["tag_name"]
    if not re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+", tag):
        raise ValueError("Unexpected Deno release tag.")

    if binary.is_file():
        current = subprocess.run([str(binary), "--version"], capture_output=True, text=True, timeout=10)
        if current.returncode == 0 and (current.stdout.partition("\n")[0].strip() + " ").startswith("deno " + tag[1:] + " "):
            print(f"Deno {tag} is already current.", flush=True)
            return

    machine = platform.machine().lower()
    architecture = {"amd64": "x86_64", "x86_64": "x86_64", "arm64": "aarch64", "aarch64": "aarch64"}.get(machine)
    if not architecture:
        raise ValueError("Unsupported Windows architecture for Deno.")
    name = f"deno-{architecture}-pc-windows-msvc.zip"
    url = f"https://github.com/denoland/deno/releases/download/{tag}/{name}"
    with urlopen(url + ".sha256sum", timeout=15) as response:
        expected = response.read(1024).decode("ascii").split()[0]
    if not re.fullmatch(r"[0-9a-fA-F]{64}", expected):
        raise ValueError("Invalid Deno checksum.")
    TOOLS.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=TOOLS) as folder:
        work = Path(folder)
        archive = work / name
        digest = hashlib.sha256()
        total = 0
        with urlopen(url, timeout=30) as response, archive.open("wb") as output:
            while chunk := response.read(256 * 1024):
                total += len(chunk)
                if total > MAX_ARCHIVE:
                    raise ValueError("Deno archive exceeds the download limit.")
                output.write(chunk)
                digest.update(chunk)
        if digest.hexdigest() != expected.lower():
            raise ValueError("Deno archive checksum mismatch.")
        staged = work / "deno.exe"
        with ZipFile(archive) as package:
            entry = package.getinfo("deno.exe")
            if not 0 < entry.file_size <= 350_000_000:
                raise ValueError("Unexpected Deno binary size.")
            with package.open(entry) as source, staged.open("wb") as output:
                shutil.copyfileobj(source, output)
        subprocess.run([str(staged), "--version"], check=True, timeout=15)
        os.replace(staged, binary)


def main():
    update_ytdlp()
    try:
        update_deno()
    except (OSError, ValueError, KeyError, IndexError, BadZipFile, subprocess.SubprocessError) as exc:
        if shutil.which("deno.exe"):
            print(f"Deno update failed ({type(exc).__name__}); using the existing Deno.", file=sys.stderr)
        else:
            print(f"Deno update failed ({type(exc).__name__}); YouTube JavaScript support is unavailable.", file=sys.stderr)


if __name__ == "__main__":
    main()
