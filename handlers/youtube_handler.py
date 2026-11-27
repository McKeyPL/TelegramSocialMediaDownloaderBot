
import time
import traceback
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
import yt_dlp_wrapper
from handlers.base import MediaHandler, PostData
from media_policy import MediaPolicy


class YouTubeHandler(MediaHandler):
    SITE_NAME = "youtube"
    URL_REGEX = r"((http(s)?://)|^| )(www\.|m\.)?(youtube(-nocookie)?\.com|youtu\.be)/.+"
    MAX_FILESIZE = 123 * 1024 * 1024
    MAX_DURATION_SECONDS = 20 * 60

    def __init__(self, enabled: bool = True, *, policy: MediaPolicy | None = None):
        self.enabled = enabled
        self.policy = policy or MediaPolicy()
        if not self.enabled:
            print("YouTubeHandler is disabled.")

    def normalize_url(self, link: str) -> str:
        parsed = urlsplit(link)
        query_params = parse_qsl(parsed.query, keep_blank_values=True)
        filtered_query_params = [
            (key, value)
            for key, value in query_params
            if key not in {"si", "pp"}
        ]
        normalized_query = urlencode(filtered_query_params, doseq=True)
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, normalized_query, ""))

    def handle(self, link: str) -> PostData | None:
        try:
            info = yt_dlp_wrapper.get_video_info(link)
            max_filesize = self.policy.download_limit(self.MAX_FILESIZE)
            max_duration = self.policy.duration_limit(self.MAX_DURATION_SECONDS)
            if not yt_dlp_wrapper.is_within_download_limits(
                info, max_filesize=max_filesize, max_duration=max_duration,
            ):
                return PostData(site=self.SITE_NAME, post_type="error")

            output_filename, info_dict = yt_dlp_wrapper.download(
                link, policy=self.policy, max_filesize=max_filesize, max_duration=max_duration,
            )
            return self._prepare_metadata(output_filename, info_dict)
        except Exception as e:
            print(time.strftime("%d.%m.%Y %H:%M:%S", time.localtime()))
            traceback.print_exception(type(e), e, e.__traceback__)
            print("Couldn't get video from url: " + link)
            print()
            return None

    def _prepare_metadata(self, output_filename, info_dict) -> PostData:
        uploader = info_dict.get("uploader") or "Unknown"
        uploader_id = info_dict.get("uploader_id") or "Unknown"
        return PostData(
            site="youtube",
            post_type="media",
            post_id=info_dict.get("id", ""),
            url=info_dict.get("original_url", ""),
            author=f"{uploader} (ID: {uploader_id})",
            text=info_dict.get("fulltitle", ""),
            spoiler=False,
            media=[[output_filename, "video_file"]],
        )
