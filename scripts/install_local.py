"""Prepare the Windows local Bot API without changing the public config."""
import argparse
import getpass
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from deploy_windows import DeploymentError, extract_release


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from runtime_config import read_config


def prepare_config():
    source = ROOT / "config.txt"
    config = read_config(source if source.exists() else ROOT / "config.default.txt")
    if not config.has_section("telegram"):
        config.add_section("telegram")
    config["telegram"].update({"mode": "local", "local_binary": ""})
    for key in ("api_id", "api_hash"):
        if not config.has_option("telegram", key):
            config["telegram"][key] = ""
    destination = ROOT / "local-data" / "config.txt"
    destination.parent.mkdir(exist_ok=True)
    try:
        with destination.open("x", encoding="utf-8") as output:
            config.write(output)
    except FileExistsError:
        print("Existing local-data/config.txt preserved.")
    else:
        print("Prepared local-data/config.txt.")


def install(archive, digest):
    binary = ROOT / "bin" / "telegram-bot-api.exe"
    if archive:
        with TemporaryDirectory() as folder:
            extracted = Path(folder)
            extract_release(archive, digest.lower(), extracted)
            bundled = extracted / "bin" / binary.name
            if not bundled.is_file():
                raise ValueError("Select the windows-x64 release ZIP.")
            subprocess.run([str(bundled), "--version"], check=True, timeout=15)
            binary.parent.mkdir(exist_ok=True)
            shutil.copy2(bundled, binary)
    if not binary.is_file():
        raise ValueError("Missing Windows ZIP. Run install_local.bat --archive <zip> --sha256 <digest>.")
    if not shutil.which("ffmpeg"):
        raise ValueError("FFmpeg must be on PATH.")
    subprocess.run([str(binary), "--version"], check=True, timeout=15)

    data = ROOT / "local-data"
    destination = data / "config.txt"
    source = ROOT / "config.txt"
    config = read_config(destination if destination.exists() else source if source.exists() else ROOT / "config.default.txt")
    if not re.fullmatch(r"[0-9]+:[A-Za-z0-9_-]+", config.get("config", "token", fallback="")):
        raise ValueError("Fill in the Telegram bot token in config.txt and rerun the installer.")
    if not config.has_section("telegram"):
        config.add_section("telegram")
    telegram = config["telegram"]
    telegram["mode"] = "local"
    telegram["local_binary"] = ""
    for key, pattern in (("api_id", r"[1-9][0-9]{0,9}"), ("api_hash", r"[a-fA-F0-9]{32}")):
        value = os.environ.get(f"TELEGRAM_{key.upper()}", telegram.get(key, "")).strip()
        if not re.fullmatch(pattern, value):
            value = getpass.getpass(f"Telegram {key}: ").strip()
        if not re.fullmatch(pattern, value):
            raise ValueError(f"Invalid Telegram {key}.")
        telegram[key] = value

    data.mkdir(exist_ok=True)
    with TemporaryDirectory(dir=data) as staging:
        staged = Path(staging) / "config.txt"
        with staged.open("w", encoding="utf-8") as output:
            config.write(output)
        subprocess.run(
            [sys.executable, str(ROOT / "run_bot.py"), "--data-dir", staging, "--check"], check=True,
        )
        staged.replace(destination)
    print("Local config saved in local-data\config.txt.")
    print("For a migrated token, stop the public bot and log out before running start_bot_local.bat.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, help="Windows all-in-one release ZIP")
    parser.add_argument("--sha256", help="trusted SHA-256 for the ZIP")
    parser.add_argument("--prepare-config", action="store_true", help="create local config without installing the binary")
    args = parser.parse_args()
    if bool(args.archive) != bool(args.sha256):
        parser.error("--archive and --sha256 must be supplied together")
    try:
        if args.prepare_config:
            prepare_config()
        else:
            install(args.archive, args.sha256)
    except (DeploymentError, OSError, ValueError, subprocess.SubprocessError) as exc:
        parser.exit(1, f"Local setup failed: {exc}\n")


if __name__ == "__main__":
    main()
