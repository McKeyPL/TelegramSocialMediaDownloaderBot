import ipaddress
import re
import socket
from html import unescape
from urllib.parse import urlsplit

import requests

from .base import MediaHandler, PostData


class MastodonHandler(MediaHandler):
    SITE_NAME = "mastodon"
    URL_REGEX = r"https?://[a-z0-9.-]+/(?:@[^/\s]+/[0-9]+|notes/[a-z0-9]+)(?=[/?#\s]|$)"
    resolve_related_in_allowed_chats = True

    def handle(self, link: str) -> PostData | None:
        parts = urlsplit(link)
        status = re.fullmatch(r"/@([^/\s]+)/([0-9]+)/?", parts.path)
        note = re.fullmatch(r"/notes/([a-zA-Z0-9]+)/?", parts.path)
        if (
            parts.scheme not in {"http", "https"}
            or not re.fullmatch(r"[a-z0-9.-]+", parts.netloc, re.IGNORECASE)
            or not (status or note)
        ):
            return None

        try:
            host = parts.hostname
            if not self._is_public_host(host, 443):
                return None
            origin = f"https://{host}"
            if status:
                return self._handle_status(origin, status[1], status[2], link)
            return self._handle_note(origin, note[1], link)
        except (OSError, ValueError, requests.RequestException):
            return PostData(site=self.SITE_NAME, post_type="error")

    def _handle_status(self, origin: str, account: str, status_id: str, link: str) -> PostData:
        for api_version in ("v2", "v1"):
            response = requests.get(
                f"{origin}/api/{api_version}/statuses/{status_id}",
                timeout=5,
                allow_redirects=False,
            )
            if response.status_code == 200:
                break
        else:
            return PostData(site=self.SITE_NAME, post_type="error")

        data = response.json()
        media = []
        for attachment in data.get("media_attachments") or ():
            media_type = {"image": "photo", "video": "video", "gifv": "video"}.get(attachment.get("type"))
            url = attachment.get("url")
            if media_type and self._is_public_media_url(url):
                media.append([url, media_type])

        parent_id = str(data.get("in_reply_to_id") or "")
        reply_url = f"{origin}/@{account}/{parent_id}" if re.fullmatch(r"[0-9]+", parent_id) else ""
        return PostData(
            site=self.SITE_NAME,
            post_type="media" if media else "text",
            post_id=status_id,
            text=self._clean_text(data.get("content")),
            author=(data.get("account") or {}).get("acct", ""),
            url=data.get("url") or link,
            media=media,
            reply=bool(reply_url),
            reply_url=reply_url,
        )

    def _handle_note(self, origin: str, note_id: str, link: str) -> PostData:
        response = requests.post(
            f"{origin}/api/notes/show",
            json={"noteId": note_id},
            timeout=5,
            allow_redirects=False,
        )
        if response.status_code != 200:
            return PostData(site=self.SITE_NAME, post_type="error")

        data = response.json()
        media = []
        for attachment in data.get("files") or ():
            mime = (attachment.get("type") or "").partition("/")[0]
            media_type = {"image": "photo", "video": "video"}.get(mime)
            url = attachment.get("url")
            if media_type and self._is_public_media_url(url):
                media.append([url, media_type])

        return PostData(
            site=self.SITE_NAME,
            post_type="media" if media else "text",
            post_id=note_id,
            text=self._clean_text(data.get("text")),
            author=(data.get("user") or {}).get("username", ""),
            url=link,
            media=media,
            spoiler=bool(data.get("cw")),
        )

    @staticmethod
    def _is_public_host(host: str | None, port: int) -> bool:
        if not host:
            return False
        addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        return bool(addresses) and all(
            ipaddress.ip_address(address[4][0]).is_global for address in addresses
        )

    @classmethod
    def _is_public_media_url(cls, url: str | None) -> bool:
        if not url:
            return False
        try:
            parts = urlsplit(url)
            return (
                parts.scheme in {"http", "https"}
                and not parts.username
                and not parts.password
                and cls._is_public_host(parts.hostname, parts.port or (443 if parts.scheme == "https" else 80))
            )
        except (OSError, ValueError):
            return False

    @staticmethod
    def _clean_text(text: str | None) -> str:
        text = re.sub(r"<br\s*/?>|</p\s*>", "\n", text or "", flags=re.IGNORECASE)
        text = unescape(re.sub(r"<[^>]+>", "", text))
        return re.sub(r"\n\s*\n+", "\n\n", text).strip()
