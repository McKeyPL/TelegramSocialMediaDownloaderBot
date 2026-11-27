import contextlib
import configparser
import io
import re
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import bot
from handlers import HandlerRegistry, YouTubeHandler
from handlers import youtube_handler
from handlers.base import PostData
from handlers.bluesky_handler import BlueskyHandler
from handlers.mastodon_handler import MastodonHandler
from handlers.streamable_handler import StreamableHandler
from media_policy import MediaPolicy
from post_data_sender import PostDataSender
from telegram_routes import TelegramRoutes


ROOT = Path(__file__).resolve().parents[1]
YOUTUBE_URL = "https://www.youtube.com/watch?v=video"
STREAMABLE_URL = "https://streamable.com/example"
BLUESKY_URL = "https://bsky.app/profile/alice.example/post/example"
MASTODON_URL = "https://social.example/@alice/2"
MISSKEY_URL = "https://notes.example/notes/example"
PHOTO_URL = "https://media.example/photo.jpg"


def response(data=None, status=200):
    result = Mock(status_code=status)
    result.json.return_value = data
    return result


def mastodon_status(text, parent=None, media=None):
    return {
        "content": f"<p>{text}</p>",
        "account": {"acct": "alice"},
        "in_reply_to_id": parent,
        "media_attachments": media or [],
    }


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.config = configparser.ConfigParser()
        self.config.read(ROOT / "config.default.txt", encoding="utf-8")
        self.telegram = Mock()
        self.telegram.get_me.return_value.id = 99
        self.sender = PostDataSender(self.telegram, "Can't download this post")
        self.routes = []
        self.telegram.message_handler.side_effect = self.register_route
        photo_download = patch("post_data_sender.file_downloader.download_video", side_effect=self.download_photo)
        self.photo_download = photo_download.start()
        self.addCleanup(photo_download.stop)

    @staticmethod
    def download_photo(url, site, post_id, *, directory, max_bytes):
        path = Path(directory, f"{post_id}.jpg")
        path.write_bytes(b"photo")
        return str(path)

    def register_route(self, **options):
        def register(handler):
            self.routes.append((options, handler))
            return handler
        return register

    def dispatch(self, url):
        message = SimpleNamespace(
            text=url, from_user=SimpleNamespace(id=7), chat=SimpleNamespace(id=-10002137),
            message_id=10, id=10, forward_origin=None,
        )
        for options, handler in self.routes:
            pattern = options.get("regexp")
            if pattern and re.search(pattern, url, flags=re.IGNORECASE) and options["func"](message):
                handler(message)
                return
        self.fail(f"No Telegram route matched {url}")

    def start_offline_bot(self):
        with (
            patch.object(bot, "load_config_or_exit", return_value=self.config),
            patch.object(bot, "create_bot", return_value=self.telegram),
            patch.object(bot.singleton, "SingleInstance"),
            patch.object(bot, "run_polling_forever"),
            patch.object(bot, "register_shutdown_signal_handlers"),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            bot.main()

    def test_allowed_users_all_and_restricted_chat(self):
        message = SimpleNamespace(from_user=SimpleNamespace(id=7), chat=SimpleNamespace(id=8))
        all_users = TelegramRoutes(self.telegram, self.config, None, [], 99, [], "", Mock())
        restricted = TelegramRoutes(self.telegram, self.config, [1], [8], 99, [], "", Mock())
        self.assertTrue(all_users._is_allowed_user(message))
        self.assertTrue(restricted._is_allowed_user(message))
        message.chat.id = 9
        self.assertFalse(restricted._is_allowed_user(message))

    def test_main_accepts_legacy_config_and_registers_routes(self):
        self.config.remove_option("instagram", "enabled")
        self.config.remove_option("instagram", "user_agent")
        with (
            patch.object(bot, "load_config_or_exit", return_value=self.config),
            patch.object(bot, "create_bot", return_value=self.telegram),
            patch.object(bot.singleton, "SingleInstance"),
            patch.object(bot, "run_polling_forever") as polling,
            patch.object(bot, "register_shutdown_signal_handlers"),
            patch("handlers.instagram_handler.Client") as instagram,
            contextlib.redirect_stdout(io.StringIO()),
        ):
            instagram.return_value.load_settings.side_effect = FileNotFoundError
            bot.main()
        polling.assert_called_once_with(self.telegram)
        instagram.return_value.set_user_agent.assert_not_called()
        self.assertTrue(self.telegram.message_handler.call_args_list)

    def test_site_routing(self):
        self.config["instagram"]["enabled"] = "no"
        self.config["bluesky"]["enabled"] = "yes"
        with contextlib.redirect_stdout(io.StringIO()):
            registry = HandlerRegistry.create_from_config(self.config)
        for site, url in {
            "youtube": YOUTUBE_URL,
            "booru": "https://derpibooru.org/images/1",
            "9gag": "https://9gag.com/gag/example",
            "mastodon": MASTODON_URL,
            "streamable": STREAMABLE_URL,
            "bluesky": BLUESKY_URL,
        }.items():
            with self.subTest(site=site):
                self.assertIsNotNone(re.match(registry.get_handler(site).URL_REGEX, url))
        self.assertIsNotNone(re.match(registry.get_handler("mastodon").URL_REGEX, MISSKEY_URL))

    def test_photo_sends_to_original_chat_and_replies_to_message(self):
        message = SimpleNamespace(chat=SimpleNamespace(id=8), message_id=10, id=10)
        self.sender.send(message, PostData(
            site="bluesky", post_type="media", media=[[PHOTO_URL, "photo"]],
            text="Photo", spoiler=True,
        ))
        kwargs = self.telegram.send_photo.call_args.kwargs
        self.assertEqual(kwargs["chat_id"], 8)
        self.assertTrue(kwargs["photo"].closed)
        self.assertEqual(self.photo_download.call_args.args[0], PHOTO_URL)
        self.assertEqual(kwargs["reply_parameters"].message_id, 10)
        self.assertTrue(kwargs["has_spoiler"])

    def test_youtube_limits_and_missing_uploader(self):
        handler = YouTubeHandler()
        with (
            patch.object(youtube_handler.yt_dlp_wrapper, "get_video_info") as info,
            patch.object(youtube_handler.yt_dlp_wrapper, "download") as download,
            contextlib.redirect_stdout(io.StringIO()),
        ):
            for oversized in ({"filesize": 123 * 1024 * 1024 + 1}, {"duration": 1201}):
                info.return_value = oversized
                self.assertEqual(handler.handle(YOUTUBE_URL).post_type, "error")
            download.assert_not_called()
            info.return_value = {"filesize": 123 * 1024 * 1024, "duration": 1200}
            download.return_value = ("video.mp4", {"id": "video", "original_url": YOUTUBE_URL})
            clean_url = handler.normalize_url(YOUTUBE_URL + "&si=tracking&pp=tracking#fragment")
            post = handler.handle(clean_url)
            self.assertEqual(post.author, "Unknown (ID: Unknown)")
            self.assertEqual(post.media, [["video.mp4", "video_file"]])
            download.assert_called_once_with(
                YOUTUBE_URL, policy=MediaPolicy(), max_filesize=123 * 1024 * 1024, max_duration=1200,
            )

    def test_mastodon_ancestors_precede_requested_post(self):
        def get(url, **kwargs):
            if "/v2/" in url:
                return response(status=404)
            if url.endswith("/2"):
                return response(mastodon_status("Child", parent="1"))
            return response(mastodon_status("Parent", media=[{"type": "image", "url": PHOTO_URL}]))

        self.config["instagram"]["enabled"] = "no"
        self.start_offline_bot()
        self.telegram.send_photo.return_value.message_id = 11
        with (
            patch("handlers.mastodon_handler.socket.getaddrinfo", return_value=[
                (None, None, None, None, ("93.184.215.14", 443)),
            ]),
            patch("handlers.mastodon_handler.requests.get", side_effect=get),
        ):
            self.dispatch(MASTODON_URL)
        self.assertEqual(self.photo_download.call_args.args[0], PHOTO_URL)
        self.assertTrue(self.telegram.send_photo.call_args.kwargs["photo"].closed)
        kwargs = self.telegram.send_message.call_args.kwargs
        self.assertIn("Child", kwargs["text"])
        self.assertEqual(kwargs["reply_parameters"].message_id, 11)

    def test_misskey_media_and_spoiler(self):
        self.config["instagram"]["enabled"] = "no"
        self.start_offline_bot()
        note = {"user": {"username": "alice"}, "text": "Photo", "cw": "Warning",
                "files": [{"type": "image/jpeg", "url": PHOTO_URL}]}
        with (
            patch("handlers.mastodon_handler.socket.getaddrinfo", return_value=[
                (None, None, None, None, ("93.184.215.14", 443)),
            ]),
            patch("handlers.mastodon_handler.requests.post", return_value=response(note)) as fetch,
        ):
            self.dispatch(MISSKEY_URL)
        kwargs = self.telegram.send_photo.call_args.kwargs
        self.assertEqual(self.photo_download.call_args.args[0], PHOTO_URL)
        self.assertTrue(kwargs["photo"].closed)
        self.assertTrue(kwargs["has_spoiler"])
        self.assertEqual(fetch.call_args.kwargs["json"], {"noteId": "example"})

    def test_federated_handler_does_not_contact_non_public_hosts(self):
        with (
            patch("handlers.mastodon_handler.socket.getaddrinfo", return_value=[
                (None, None, None, None, ("127.0.0.1", 443)),
            ]),
            patch("handlers.mastodon_handler.requests.get") as fetch,
        ):
            self.assertIsNone(MastodonHandler().handle(MASTODON_URL))
        fetch.assert_not_called()

    def test_streamable_disabled_and_enabled(self):
        disabled = StreamableHandler(enabled=False)
        self.config["instagram"]["enabled"] = "no"
        self.start_offline_bot()
        link = STREAMABLE_URL + "?key=value"
        with (
            tempfile.TemporaryDirectory() as temp_dir,
            patch("yt_dlp_wrapper.get_video_info", return_value={}) as info,
            patch("yt_dlp_wrapper.download") as download,
            contextlib.redirect_stdout(io.StringIO()),
        ):
            self.assertIsNone(disabled.handle(link))
            info.assert_not_called()
            filename = Path(temp_dir, "clip.mp4")
            filename.write_bytes(b"test media")
            download.return_value = (filename, {"id": "example", "original_url": link, "title": "Clip"})
            self.telegram.send_video.side_effect = lambda **kwargs: kwargs["video"].read()
            self.dispatch(link)
            download.assert_called_once_with(
                link, policy=MediaPolicy(), max_filesize=100 * 1024 * 1024, max_duration=1200,
            )
            self.assertEqual(self.telegram.send_video.call_args.kwargs["chat_id"], -10002137)
            self.assertTrue(self.telegram.send_video.call_args.kwargs["video"].closed)

    def test_bluesky_authenticated_photo(self):
        self.config["instagram"]["enabled"] = "no"
        self.config["bluesky"] = {
            "enabled": "yes", "username": "alice.example", "app_password": "test-password",
        }
        self.start_offline_bot()
        post = {
            "cid": "example", "record": {"text": "Text"}, "author": {"handle": "alice.example"},
            "embed": {"$type": "app.bsky.embed.images#view", "images": [{"fullsize": PHOTO_URL}]},
        }
        with (
            patch("handlers.bluesky_handler.requests.post", return_value=response({"accessJwt": "test-token"})) as login,
            patch("handlers.bluesky_handler.requests.get", return_value=response({"thread": {"post": post}})) as get,
        ):
            self.dispatch(BLUESKY_URL)
        self.assertEqual(self.photo_download.call_args.args[0], PHOTO_URL)
        self.assertTrue(self.telegram.send_photo.call_args.kwargs["photo"].closed)
        self.assertEqual(login.call_args.kwargs["json"]["identifier"], "alice.example")
        self.assertEqual(get.call_args.kwargs["headers"]["Authorization"], "Bearer test-token")

    def test_bluesky_nested_playlist_uses_downloaded_file(self):
        handler = BlueskyHandler(enabled=True, username="alice.example", app_password="test-password")
        playlist = "https://video.example/playlist.m3u8"
        post = {"cid": "example", "embed": {
            "$type": "app.bsky.embed.recordWithMedia#view",
            "media": {"$type": "app.bsky.embed.video#view", "playlist": playlist},
        }}
        with (
            tempfile.TemporaryDirectory() as temp_dir,
            patch("handlers.bluesky_handler.requests.post", return_value=response({"accessJwt": "test-token"})),
            patch("handlers.bluesky_handler.requests.get", return_value=response({"thread": {"post": post}})),
            patch("yt_dlp_wrapper.download") as download,
        ):
            filename = Path(temp_dir, "clip.mp4")
            filename.touch()
            download.return_value = (str(filename), {})
            result = handler.handle(BLUESKY_URL)
        self.assertEqual(result.media, [[str(filename), "video_file"]])
        download.assert_called_once_with(
            playlist, output_template="video.bsky.example.%(ext)s", thumbnail_url=None,
            policy=MediaPolicy(), max_duration=None,
        )
