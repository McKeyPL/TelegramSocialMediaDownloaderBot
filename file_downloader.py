import re
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

import urllib3

from media_policy import MediaLimitError, PUBLIC_UPLOAD_BYTES
from outbound_http import open_public_url, parse_web_url


DOWNLOAD_TIMEOUT = (8, 45)
CHUNK_SIZE = 256 * 1024


def download_video(
    url: str, site: str, post_id: str, *, max_bytes: int = PUBLIC_UPLOAD_BYTES, directory: Path | None = None,
) -> str:
    if not re.fullmatch(r"[a-zA-Z0-9_-]+", site):
        return ""

    try:
        parts = parse_web_url(url)
        suffix = Path(parts.path).suffix
        if not re.fullmatch(r"\.[a-zA-Z0-9]{1,10}", suffix):
            suffix = ".mp4"
        cache_key = sha256(f"{post_id}\n{url}".encode()).hexdigest()
        site_dir = directory if directory is not None else Path("temp") / site
        final_path = site_dir / f"{cache_key}{suffix}"
        if final_path.is_file():
            if final_path.stat().st_size > max_bytes:
                raise MediaLimitError("Cached media exceeds the configured size limit")
            return str(final_path)

        site_dir.mkdir(parents=True, exist_ok=True)
        temp_path = site_dir / f"{cache_key}.{uuid4().hex}.tmp{suffix}"
        try:
            with open_public_url(url, timeout=DOWNLOAD_TIMEOUT) as response, temp_path.open("xb") as output:
                content_length = response.headers.get("Content-Length") or ""
                if content_length.isdecimal() and int(content_length) > max_bytes:
                    raise MediaLimitError("Media exceeds the configured size limit")
                downloaded = 0
                for chunk in response.stream(CHUNK_SIZE):
                    downloaded += len(chunk)
                    if downloaded > max_bytes:
                        raise MediaLimitError("Media exceeds the configured size limit")
                    output.write(chunk)
                if downloaded == 0:
                    raise ValueError("Downloaded media is empty")

            try:
                temp_path.replace(final_path)
            except PermissionError:
                # Windows can refuse replacement while another worker is reading the cached file.
                if not final_path.is_file():
                    raise
            return str(final_path)
        finally:
            temp_path.unlink(missing_ok=True)
    except (OSError, ValueError, urllib3.exceptions.HTTPError) as exc:
        print(f"Couldn't download video for {site} ({type(exc).__name__}).")
        return ""
