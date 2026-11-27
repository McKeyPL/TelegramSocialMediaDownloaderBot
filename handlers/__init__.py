"""Handler modules for different social media platforms."""

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
from .registry import HandlerRegistry

__all__ = [
    'BlueskyHandler',
    'BooruHandler',
    'DemotyHandler',
    'InstagramHandler',
    'MastodonHandler',
    'NineGagHandler',
    'StreamableHandler',
    'TikTokHandler',
    'TwitterHandler',
    'YouTubeHandler',
    'HandlerRegistry',
]
