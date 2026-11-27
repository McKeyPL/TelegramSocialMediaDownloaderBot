import traceback
from urllib.parse import urlsplit, urlunsplit

import yt_dlp_wrapper
from media_policy import MediaPolicy

from .base import MediaHandler, PostData


class StreamableHandler(MediaHandler):
    SITE_NAME = "streamable"
    URL_REGEX = r"((http(s)?://)|^| )(www\.)?streamable\.com/.+"
    MAX_FILESIZE = 100 * 1024 * 1024
    MAX_DURATION_SECONDS = 20 * 60

    def __init__(self, enabled: bool, *, policy: MediaPolicy | None = None):
        self.enabled = enabled
        self.policy = policy or MediaPolicy()

    def normalize_url(self, link: str) -> str:
        return urlunsplit(urlsplit(link)._replace(fragment=""))

    def handle(self, link: str) -> PostData | None:
        if not self.enabled:
            return None

        try:
            info = yt_dlp_wrapper.get_video_info(link)
            max_filesize = self.policy.download_limit(self.MAX_FILESIZE)
            max_duration = self.policy.duration_limit(self.MAX_DURATION_SECONDS)
            if yt_dlp_wrapper.is_within_download_limits(
                info, max_filesize=None if self.policy.local else max_filesize, max_duration=max_duration,
            ):
                filename, info = yt_dlp_wrapper.download(
                    link, policy=self.policy, max_filesize=max_filesize, max_duration=max_duration,
                )
                return PostData(
                    site=self.SITE_NAME,
                    post_type="media",
                    post_id=info.get("id"),
                    url=info.get("original_url") or link,
                    text=info.get("title") or info.get("fulltitle") or "",
                    media=[[filename, "video_file"]],
                )
        except Exception:
            traceback.print_exc()

        return PostData(site=self.SITE_NAME, post_type="error")
