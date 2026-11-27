from enum import Enum
from pathlib import Path
import shutil
from tempfile import mkdtemp

import ffmpeg
import os
import yt_dlp
from yt_dlp.utils import DownloadError

from media_policy import MediaLimitError, MediaPolicy


class CompressionLevel(Enum):
    NO_RESIZE = 0
    RESIZE_480 = 480
    RESIZE_360 = 360


ydl_options = {
    "format": "bestvideo[height<=720][width>720][vcodec^=avc][ext=mp4]+bestaudio/"
              "bestvideo[width<=720][vcodec^=avc][ext=mp4]+bestaudio/"
              "best[height<=720][width>720]/"
              "best[width<=720]",
    "merge_output_format": "mp4",
    "noplaylist": True,
    "hls_prefer_native": True,
    "socket_timeout": 45,
}

DOWNLOAD_ROOT = Path("temp", "yt-dlp")


compression_scenarios = [
    {
        "name": "hq",
        "size_threshold": 85 * 1000 * 1000,
        "resolution_resize": CompressionLevel.NO_RESIZE,
    },
    {
        "name": "mq",
        "size_threshold": 175 * 1000 * 1000,
        "resolution_resize": CompressionLevel.RESIZE_480,
    },
    {
        "name": "lq",
        "size_threshold": 500 * 1000 * 1000,
        "resolution_resize": CompressionLevel.RESIZE_360,
    },
]


def get_video_info(link):
    with yt_dlp.YoutubeDL(ydl_options) as ydl:
        return ydl.extract_info(link, download=False)


def is_within_download_limits(info, *, max_filesize: int, max_duration: int | None) -> bool:
    if not isinstance(info, dict):
        return False

    filesize = info.get("filesize") or info.get("filesize_approx")
    if not filesize:
        selected = info.get("requested_formats")
        if selected:
            filesize = sum(item.get("filesize") or item.get("filesize_approx") or 0 for item in selected)
        else:
            estimates = (item.get("filesize") or item.get("filesize_approx") for item in info.get("formats") or ())
            filesize = next((estimate for estimate in estimates if estimate), 0)

    return (filesize or 0) <= max_filesize and (
        max_duration is None or (info.get("duration") or 0) <= max_duration
    )


def clean_download(filename) -> None:
    """Remove the private staging directory of one yt-dlp download."""
    path = Path(filename).resolve()
    root = DOWNLOAD_ROOT.resolve()
    directory = path.parent
    if directory.parent == root and directory.name.startswith("download-"):
        shutil.rmtree(directory, ignore_errors=True)


def download(
    link, output_template=None, thumbnail_url=None, *,
    policy: MediaPolicy | None = None, max_filesize: int | None = None, max_duration: int | None = None,
):
    policy = policy or MediaPolicy()
    max_filesize = policy.download_limit() if max_filesize is None else max_filesize
    upload_limit = policy.upload_limit()
    DOWNLOAD_ROOT.mkdir(parents=True, exist_ok=True)
    directory = Path(mkdtemp(prefix="download-", dir=DOWNLOAD_ROOT)).resolve()

    def check_progress(status):
        if (status.get("downloaded_bytes") or 0) > max_filesize:
            raise DownloadError("Media exceeded the configured download size limit")
        # Split streams can each stay below the limit but exceed it together.
        total = sum(path.stat().st_size for path in directory.iterdir() if path.is_file())
        if total > max_filesize:
            raise DownloadError("Combined media exceeded the configured download size limit")

    def match_filter(info, *, incomplete=False):
        if not incomplete and not is_within_download_limits(info, max_filesize=max_filesize, max_duration=max_duration):
            return "Media exceeds the configured download size or duration limit"
        return None

    options = {
        **ydl_options,
        "outtmpl": str(directory / (output_template or "%(webpage_url_domain)s-%(id)s.%(ext)s")),
        "max_filesize": max_filesize,
        "progress_hooks": [check_progress],
        "match_filter": match_filter,
        # A stream delegated to FFmpeg can bypass yt-dlp's byte progress hooks.
        "external_downloader_args": {"ffmpeg_o": ["-fs", str(max_filesize + 1)]},
    }
    if thumbnail_url:
        options["writethumbnail"] = True
        options["postprocessors"] = [{"key": "FFmpegThumbnailsConvertor", "format": "jpg"}]

    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(link, download=True)
            if not info:
                raise MediaLimitError("yt-dlp did not produce downloadable media")
            downloads = info.get("requested_downloads") or ()
            actual = downloads[0].get("filepath") if downloads else None
            path = Path(actual or ydl.prepare_filename(info)).resolve()
            if path.parent != directory:
                raise MediaLimitError("Download did not produce a file in the staging directory")
            size = path.stat().st_size
            if not 0 < size <= max_filesize:
                raise MediaLimitError("Media exceeded the configured download size limit")

        # Preserve the public API's compression behavior. Local mode sends the original file.
        if not policy.local and ((info.get("vcodec") or "").startswith("vp09") or size > upload_limit):
            vertical = (info.get("height") or 0) > (info.get("width") or 0)
            path = Path(compress_video(str(path), size, vertical, upload_limit=upload_limit))
        if not 0 < path.stat().st_size <= upload_limit:
            raise MediaLimitError("Media exceeded the configured upload size limit")
        return str(path), info
    except BaseException:
        shutil.rmtree(directory, ignore_errors=True)
        raise


def compress_video(video_path, original_size, is_vertical=False, *, upload_limit=50 * 1000 * 1000):
    print(f"[DEBUG] compress_video called: {video_path}, size: {original_size}, is_vertical: {is_vertical}")
    for scenario in compression_scenarios:
        if original_size < scenario["size_threshold"]:
            source = Path(video_path)
            compressed_video_path = str(source.with_name(f"{source.stem}-compressed-{scenario['name']}.mp4"))
            if not os.path.exists(compressed_video_path):
                if scenario["resolution_resize"] == CompressionLevel.NO_RESIZE:
                    run_compression(video_path, compressed_video_path)
                else:
                    max_size = scenario['resolution_resize'].value
                    scale = f"scale={max_size}:-2" if is_vertical else f"scale=-2:{max_size}"
                    run_compression_with_resizing(
                        video_path, compressed_video_path, scale)
            compressed_size = os.path.getsize(compressed_video_path)
            print(f"[DEBUG] Compressed file size: {compressed_size}")
            if compressed_size <= upload_limit:
                return compressed_video_path

    if original_size <= upload_limit:
        # fallback to original video if compression fails
        print(f"[DEBUG] Compression failed, using original file.")
        return video_path

    print(f"[DEBUG] Video is too large to compress")
    raise Exception("Video is too large to compress")


def run_compression(video_path, output_file):
    print(f"[DEBUG] run_compression: {video_path} -> {output_file}")
    ffmpeg\
        .input(video_path)\
        .output(output_file,
                vcodec="libx264",
                crf=28,
                preset="slow",
                acodec="aac",
                audio_bitrate="128k")\
        .overwrite_output()\
        .run()


def run_compression_with_resizing(video_path, output_file, scale):
    print(f"[DEBUG] run_compression_with_resizing: {video_path} -> {output_file}, scale: {scale}")
    ffmpeg\
        .input(video_path)\
        .output(output_file,
                vcodec="libx264",
                crf=28,
                preset="slow",
                acodec="aac",
                audio_bitrate="128k",
                vf=scale)\
        .overwrite_output()\
        .run()
