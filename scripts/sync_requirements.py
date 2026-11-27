"""Install Python dependencies when their requirements have changed."""

import hashlib
import subprocess
import sys
from pathlib import Path


def main():
    requirements = Path(__file__).resolve().parents[1] / "requirements.txt"
    digest = hashlib.sha256(requirements.read_bytes()).hexdigest()
    marker = Path(sys.prefix) / ".telegram-downloader-requirements"
    if marker.is_file() and marker.read_text(encoding="ascii").strip() == digest:
        return
    subprocess.run([sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "-r", str(requirements)], check=True)
    subprocess.run([sys.executable, "-m", "pip", "check"], check=True)
    marker.write_text(digest + "\n", encoding="ascii")


if __name__ == "__main__":
    main()
