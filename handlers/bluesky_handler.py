import re
from pathlib import Path
from urllib.parse import urlencode, urlsplit

import requests

import yt_dlp_wrapper
from media_policy import MediaPolicy
from .base import MediaHandler, PostData


class BlueskyHandler(MediaHandler):
    SITE_NAME = "bluesky"
    URL_REGEX = r"((http(s)?://)|^| )(www\.)?bsky\.app/.+"
    POST_PATH = r"/profile/([a-zA-Z0-9.:%_-]+)/post/([a-zA-Z0-9._~-]+)/?"
    API = "https://bsky.social/xrpc"
    TIMEOUT = 10

    def __init__(self, *, enabled: bool, username: str, app_password: str, policy: MediaPolicy | None = None):
        self.enabled = enabled
        self.username = username
        self.app_password = app_password
        self.policy = policy or MediaPolicy()

    def normalize_url(self, link: str) -> str:
        if "://" not in link:
            link = f"https://{link}"
        return super().normalize_url(link)

    def handle(self, link: str) -> PostData | None:
        parts = urlsplit(link)
        path = re.fullmatch(self.POST_PATH, parts.path)
        if not self.enabled or parts.hostname not in {"bsky.app", "www.bsky.app"} or not path:
            return None

        try:
            session = requests.post(
                f"{self.API}/com.atproto.server.createSession",
                json={"identifier": self.username, "password": self.app_password},
                timeout=self.TIMEOUT,
                allow_redirects=False,
            )
            if session.status_code != 200:
                return PostData(site=self.SITE_NAME, post_type="error")
            token = session.json().get("accessJwt")
            if not token:
                return PostData(site=self.SITE_NAME, post_type="error")

            response = requests.get(
                f"{self.API}/app.bsky.feed.getPostThread",
                headers={"Authorization": f"Bearer {token}", "User-Agent": "Telegram Social Media Downloader Bot"},
                params={"uri": f"at://{path[1]}/app.bsky.feed.post/{path[2]}"},
                timeout=self.TIMEOUT,
                allow_redirects=False,
            )
            if response.status_code == 200:
                thread = response.json().get("thread") or {}
                post = thread.get("post")
                if post:
                    return self._parse_post(post, link)
        except (requests.RequestException, ValueError):
            pass

        return PostData(site=self.SITE_NAME, post_type="error")

    def _parse_post(self, post: dict, link: str) -> PostData:
        record = post.get("record") or {}
        author = post.get("author") or {}
        handle = author.get("handle")
        display_name = author.get("displayName")
        author_label = f"@{handle}" if handle else ""
        if handle and display_name:
            author_label = f"{display_name} ({author_label})"

        reply = record.get("reply") or {}
        parent = reply.get("parent") or reply.get("root") or {}
        reply_url = self._post_url(parent.get("uri", ""))
        result = PostData(
            site=self.SITE_NAME,
            post_type="text",
            post_id=post.get("cid"),
            url=link,
            text=record.get("text") or "",
            author=author_label,
            reply=bool(reply_url),
            reply_url=reply_url,
        )

        view = post.get("embed") or {}
        record_embed = record.get("embed") or {}
        did = author.get("did", "")
        self._add_media(result, view, did)
        if not result.media and not view.get("$type", "").startswith("app.bsky.embed.external"):
            self._add_media(result, record_embed, did)
        if result.media:
            result.post_type = "media"

        quote = (view.get("record") or {}).get("record") or view.get("record") or {}
        quote_record = quote.get("value") or quote.get("record") or {}
        if not quote_record:
            quote = (record_embed.get("record") or {}).get("record") or {}
            quote_record = quote.get("record") or {}
        if quote_record.get("text"):
            quoted_author = (quote.get("author") or {}).get("handle", "")
            result.text += f"\n\nQuoted post from @{quoted_author}:\n{quote_record['text']}"
        return result

    def _add_media(self, post: PostData, embed: dict, did: str) -> None:
        kind = embed.get("$type", "")
        if kind.startswith("app.bsky.embed.recordWithMedia"):
            embed = embed.get("media") or {}
            kind = embed.get("$type", "")

        if kind.startswith("app.bsky.embed.images"):
            for image in embed.get("images") or ():
                url = image.get("fullsize") or image.get("thumb") or self._blob_url(did, image.get("image"))
                if url:
                    post.media.append([url, "photo"])
                    if image.get("alt"):
                        post.text += f"\n[Image: {image['alt']}]"
        elif kind.startswith("app.bsky.embed.video"):
            video = embed.get("video") or {}
            url = embed.get("playlist") or video.get("url") or self._blob_url(did, video)
            if url and urlsplit(url).scheme == "https":
                if embed.get("alt"):
                    post.text += f"\n[Video: {embed['alt']}]"
                video_media = self._video_media(post.post_id, url, embed.get("thumbnail"))
                if video_media:
                    post.media.append(video_media)
        elif kind.startswith("app.bsky.embed.external"):
            external = embed.get("external") or {}
            if external.get("uri"):
                post.text += f"\n\nExternal link: {external['uri']}"
            thumb = external.get("thumb")
            url = thumb if isinstance(thumb, str) else self._blob_url(did, thumb)
            if url:
                post.media.append([url, "photo"])

    @staticmethod
    def _post_url(uri: str) -> str:
        match = re.fullmatch(r"at://([^/]+)/app\.bsky\.feed\.post/([^/]+)", uri)
        return f"https://bsky.app/profile/{match[1]}/post/{match[2]}" if match else ""

    @classmethod
    def _blob_url(cls, did: str, blob: dict | None) -> str:
        cid = ((blob or {}).get("ref") or {}).get("$link")
        if not did or not cid:
            return ""
        return f"{cls.API}/com.atproto.sync.getBlob?" + urlencode({"did": did, "cid": cid})

    def _video_media(self, post_id: str | None, url: str, thumbnail: str | None) -> list[str] | None:
        parsed = urlsplit(url)
        if parsed.scheme == "https" and (
            parsed.path.endswith(".m3u8") or "playlist" in parsed.path or "/watch/" in parsed.path
        ):
            template = None
            if post_id and re.fullmatch(r"[a-zA-Z0-9_-]+", post_id):
                template = f"video.bsky.{post_id}.%(ext)s"
            try:
                filename, _ = yt_dlp_wrapper.download(
                    url, output_template=template, thumbnail_url=thumbnail,
                    policy=self.policy, max_duration=self.policy.duration_limit(),
                )
                if filename and Path(filename).is_file():
                    return [filename, "video_file"]
            except Exception:
                print("Couldn't download Bluesky playlist.")
            # A playlist is not a video file and cannot be sent directly.
            return None
        return [url, "video"]
