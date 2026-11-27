import json
from configparser import ConfigParser

from media_policy import MediaPolicy
from .base import MediaHandler
from .bluesky_handler import BlueskyHandler
from .booru_handler import BooruHandler
from .demoty_handler import DemotyHandler
from .instagram_handler import InstagramHandler
from .mastodon_handler import MastodonHandler
from .ninegag_handler import NineGagHandler
from .streamable_handler import StreamableHandler
from .tiktok_handler import TikTokHandler
from .twitter_handler import TwitterHandler
from .youtube_handler import YouTubeHandler


class HandlerRegistry:
    def __init__(self, handlers: list[MediaHandler]):
        self._handlers = handlers

    @classmethod
    def create_from_config(cls, config: ConfigParser, *, media_policy: MediaPolicy | None = None):
        policy = media_policy or MediaPolicy.from_config(config)
        configured = config.get("sites", "enabled", fallback="").strip()
        enabled_sites = None
        if configured:
            known_sites = {
                handler.SITE_NAME for handler in (
                    BooruHandler, InstagramHandler, NineGagHandler, TikTokHandler, TwitterHandler,
                    DemotyHandler, YouTubeHandler, MastodonHandler, StreamableHandler, BlueskyHandler,
                )
            }
            enabled_sites = json.loads(configured)
            if not isinstance(enabled_sites, list) or any(
                not isinstance(site, str) or site not in known_sites for site in enabled_sites
            ):
                raise ValueError("[sites] enabled must be a JSON list of known site names.")

        handlers = [
            BooruHandler(),
            InstagramHandler.create_from_config(config['instagram'])
            if enabled_sites is None or InstagramHandler.SITE_NAME in enabled_sites
            else InstagramHandler(None, enabled=False),
            NineGagHandler(
                use_selenium=config['9gag'].getboolean('use_selenium')),
            TikTokHandler(),
            TwitterHandler(),
            DemotyHandler(),
            YouTubeHandler(enabled=config['youtube'].getboolean('enabled'), policy=policy),
            MastodonHandler(),
            StreamableHandler(enabled=config.getboolean("streamable", "enabled", fallback=False), policy=policy),
            BlueskyHandler(
                enabled=config.getboolean("bluesky", "enabled", fallback=False),
                username=config.get("bluesky", "username", fallback=""),
                app_password=config.get("bluesky", "app_password", fallback=""),
                policy=policy,
            ),
        ]
        if enabled_sites is not None:
            for handler in handlers:
                if handler.SITE_NAME not in enabled_sites:
                    handler.enabled = False
        return cls(handlers)

    def get_all_handlers(self) -> list[MediaHandler]:
        return self._handlers

    def get_active_handlers(self) -> list[MediaHandler]:
        return [handler for handler in self._handlers if handler.enabled]

    def get_handler(self, site_name: str) -> MediaHandler:
        for handler in self._handlers:
            if handler.SITE_NAME == site_name:
                return handler

        raise ValueError("No handler found for site: " + site_name)

    def get_active_combined_regex(self) -> str:
        active_patterns = [
            handler.URL_REGEX for handler in self.get_active_handlers()]
        if not active_patterns:
            return r"(?!)"

        return "|".join("(?:" + pattern + ")" for pattern in active_patterns)
