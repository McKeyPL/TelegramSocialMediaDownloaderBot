import unittest
from configparser import ConfigParser
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch

from requests.exceptions import ReadTimeout
from yt_dlp.utils import DownloadError

import file_downloader
import yt_dlp_wrapper
from handlers.base import PostData
from handlers.youtube_handler import YouTubeHandler
from media_policy import LOCAL_UPLOAD_BYTES, PHOTO_UPLOAD_BYTES, PUBLIC_UPLOAD_BYTES, MediaPolicy
from post_data_sender import PostDataSender
from post_handling_policies import PostHandlingPolicies
from post_orchestrator import PostOrchestrator
from related_post_resolver import RelatedPostResolver


SOURCE = "https://example.org/post"


class MediaPolicyTests(unittest.TestCase):
    def test_config_defaults_and_local_overrides(self):
        config = ConfigParser()
        self.assertEqual(MediaPolicy.from_config(config), MediaPolicy())
        config.read_dict({"telegram": {"mode": "local", "max_upload_mb": "800", "upload_timeout": "1200"},
                          "downloads": {"max_size_mb": "1200", "max_duration_minutes": "30"}})
        policy = MediaPolicy.from_config(config)
        self.assertTrue(policy.local)
        self.assertEqual(policy.upload_limit(), 800_000_000)
        self.assertEqual(policy.upload_limit("photo"), PHOTO_UPLOAD_BYTES)
        self.assertEqual(policy.download_limit(), 1_200_000_000)
        self.assertEqual(policy.duration_limit(), 1800)
        self.assertEqual(policy.timeout, 1200)

    def test_invalid_limit_fails_startup(self):
        config = ConfigParser()
        config.read_dict({"downloads": {"max_size_mb": "-1"}})
        with self.assertRaisesRegex(ValueError, "max_size_mb"):
            MediaPolicy.from_config(config)

    def test_local_youtube_preflight_uses_local_download_limit(self):
        handler = YouTubeHandler(policy=MediaPolicy(local=True))
        with (
            patch("yt_dlp_wrapper.get_video_info") as info,
            patch("yt_dlp_wrapper.download", return_value=("clip.mp4", {"id": "clip"})) as download,
        ):
            info.return_value = {"filesize": LOCAL_UPLOAD_BYTES + 1}
            self.assertEqual(handler.handle(SOURCE).post_type, "error")
            download.assert_not_called()
            info.return_value = {"filesize": 150_000_000, "duration": 1200}
            self.assertEqual(handler.handle(SOURCE).post_type, "media")
            download.assert_called_once_with(
                SOURCE, policy=handler.policy, max_filesize=LOCAL_UPLOAD_BYTES, max_duration=1200,
            )

    def test_preflight_sums_the_selected_streams(self):
        info = {"requested_formats": [{"filesize": 8}, {"filesize_approx": 3}]}
        self.assertFalse(yt_dlp_wrapper.is_within_download_limits(info, max_filesize=10, max_duration=None))
        self.assertTrue(yt_dlp_wrapper.is_within_download_limits(info, max_filesize=11, max_duration=None))


class MediaDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.original = SimpleNamespace(chat=SimpleNamespace(id=1), id=2, message_id=2)
        self.telegram = Mock()
        self.telegram.reply_to.return_value.message_id = 3
        self.temp = TemporaryDirectory(prefix="bot media with spaces ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def new_file(self, name, size):
        path = self.root / name
        with path.open("wb") as file:
            file.truncate(size)
        return path

    def post(self, *media):
        return PostData("youtube", "media", post_id="post", text="Body", media=list(media))

    def test_public_rejects_large_file_local_sends_uri_without_reading_it(self):
        path = self.new_file("large video.mp4", PUBLIC_UPLOAD_BYTES + 1)
        data = self.post([path, "video_file"])
        sender = PostDataSender(self.telegram, "Could not send")
        sender.send(self.original, data)
        self.telegram.send_video.assert_not_called()
        self.telegram.delete_message.assert_not_called()
        self.telegram.reply_to.assert_called_once_with(self.original, "Could not send")

        sender = PostDataSender(self.telegram, "Could not send", policy=MediaPolicy(local=True))
        with patch.object(Path, "open", side_effect=AssertionError("large local upload must not open in Python")):
            sender.send(self.original, data)
        sent = self.telegram.send_video.call_args.kwargs
        self.assertEqual(sent["video"], path.resolve().as_uri())
        self.assertEqual(sent["timeout"], 900)
        self.assertIn("%20", sent["video"])
        self.assertEqual(sent["reply_parameters"].message_id, self.original.message_id)

    def test_local_downloads_remote_photo_with_method_limit_and_cleans_up(self):
        url = "https://cdn.example.org/image.jpg"
        downloaded = {}

        def get_remote(requested, site, identifier, *, directory, max_bytes):
            self.assertEqual(requested, url)
            self.assertEqual(max_bytes, PHOTO_UPLOAD_BYTES)
            path = directory / "image.jpg"
            path.write_bytes(b"image")
            downloaded["path"] = path
            return str(path)

        with patch("file_downloader.download_video", side_effect=get_remote) as get:
            sender = PostDataSender(self.telegram, "Could not send", policy=MediaPolicy(local=True))
            sender.send(self.original, self.post([url, "photo"]))
        get.assert_called_once()
        path = downloaded["path"]
        self.assertEqual(self.telegram.send_photo.call_args.kwargs["photo"], path.resolve().as_uri())
        self.assertFalse(path.parent.exists())

    def test_split_album_never_contains_a_single_item_and_preserves_reply_chain(self):
        media = [[self.new_file(f"part {number}.mp4", 1), "video_file"] for number in range(11)]
        self.telegram.send_media_group.side_effect = [
            [SimpleNamespace(message_id=10)], [SimpleNamespace(message_id=20)],
        ]
        sender = PostDataSender(self.telegram, "Could not send", policy=MediaPolicy(local=True))
        sent = sender.send(self.original, self.post(*media))
        groups = [call.kwargs for call in self.telegram.send_media_group.call_args_list]
        self.assertEqual([len(group["media"]) for group in groups], [9, 2])
        self.assertTrue(all(item.media.startswith("file:///") for group in groups for item in group["media"]))
        self.assertEqual([group["reply_parameters"].message_id for group in groups], [2, 10])
        self.assertEqual(self.telegram.send_message.call_args.kwargs["reply_parameters"].message_id, 20)
        self.assertEqual(sent, self.telegram.send_message.return_value)
        self.telegram.delete_message.assert_called_once_with(1, 2)

    def test_timeout_closes_cloud_file_and_cleans_managed_download(self):
        staging = self.root / "download-test"
        staging.mkdir()
        path = staging / "clip.mp4"
        path.write_bytes(b"clip")
        self.telegram.send_video.side_effect = ReadTimeout("timeout")
        sender = PostDataSender(self.telegram, "Could not send")
        with (
            patch.object(yt_dlp_wrapper, "DOWNLOAD_ROOT", self.root),
            self.assertRaises(ReadTimeout),
        ):
            sender.send(self.original, self.post([path, "video_file"]))
        self.assertTrue(self.telegram.send_video.call_args.kwargs["video"].closed)
        self.assertFalse(staging.exists())
        self.telegram.delete_message.assert_not_called()

    def test_uncertain_ancestor_delivery_stops_chain_and_cleans_unsent_files(self):
        staging = self.root / "download-unsent"
        staging.mkdir()
        path = staging / "clip.mp4"
        path.write_bytes(b"clip")
        child = self.post([path, "video_file"])
        child.reply = True
        child.reply_url = SOURCE + "/parent"
        parent = PostData("youtube", "text", text="Parent", url=child.reply_url)
        handler = Mock()
        handler.SITE_NAME = "youtube"
        handler.normalize_url.side_effect = lambda url: url
        handler.handle.side_effect = [child, parent]
        self.telegram.send_message.side_effect = ReadTimeout("unknown outcome")
        sender = PostDataSender(self.telegram, "Could not send")
        orchestrator = PostOrchestrator(sender, RelatedPostResolver(), [])
        with patch.object(yt_dlp_wrapper, "DOWNLOAD_ROOT", self.root):
            orchestrator.process_link_for_handler(self.original, SOURCE, handler, PostHandlingPolicies())
        self.telegram.send_video.assert_not_called()
        self.telegram.reply_to.assert_called_once()
        handler.handle_fallback.assert_not_called()
        self.assertFalse(staging.exists())

    def test_quote_is_sent_first_and_retains_reply_reference(self):
        quote_url = SOURCE + "/quoted"
        root = PostData("youtube", "text", text="Root", url=SOURCE, quote=True, quote_url=quote_url)
        quote = PostData("youtube", "text", text="Quote", url=quote_url)
        handler = Mock()
        handler.SITE_NAME = "youtube"
        handler.normalize_url.side_effect = lambda url: url
        handler.handle.side_effect = [root, quote]
        self.telegram.send_message.side_effect = [SimpleNamespace(message_id=11), SimpleNamespace(message_id=12)]
        sender = PostDataSender(self.telegram, "Could not send")
        orchestrator = PostOrchestrator(sender, RelatedPostResolver(), [])
        orchestrator.process_link_for_handler(self.original, SOURCE, handler, PostHandlingPolicies())
        sent = [call.kwargs for call in self.telegram.send_message.call_args_list]
        self.assertTrue(sent[0]["text"].startswith("Quote"))
        self.assertTrue(sent[1]["text"].startswith("Root"))
        self.assertEqual([call["reply_parameters"].message_id for call in sent], [2, 11])


class MediaDownloadTests(unittest.TestCase):
    def test_http_downloader_limits_known_and_unknown_sizes(self):
        with TemporaryDirectory() as folder:
            directory = Path(folder)
            response = Mock(headers={"Content-Length": "6"})
            response.stream.return_value = iter([b"123", b"456"])

            @contextmanager
            def open_url(*args, **kwargs):
                yield response

            with patch("file_downloader.open_public_url", side_effect=open_url):
                url = "https://example.org/video.mp4"
                self.assertEqual(file_downloader.download_video(url, "youtube", "clip", max_bytes=5, directory=directory), "")
                response.stream.assert_not_called()
                response.headers = {}
                self.assertEqual(file_downloader.download_video(url, "youtube", "clip", max_bytes=5, directory=directory), "")
                response.stream.assert_called_once()
                self.assertEqual(list(directory.iterdir()), [])

    def test_yt_dlp_aborts_unknown_size_and_cleans_staging(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)

            def make_downloader(options):
                downloader = Mock()
                downloader.__enter__ = Mock(return_value=downloader)
                downloader.__exit__ = Mock(return_value=False)

                def extract(*args, **kwargs):
                    part = Path(options["outtmpl"]).parent / "partial.part"
                    part.write_bytes(b"123456")
                    options["progress_hooks"][0]({"status": "downloading", "downloaded_bytes": 6})

                downloader.extract_info.side_effect = extract
                return downloader

            with (
                patch.object(yt_dlp_wrapper, "DOWNLOAD_ROOT", root),
                patch("yt_dlp_wrapper.yt_dlp.YoutubeDL", side_effect=make_downloader),
                self.assertRaises(DownloadError),
            ):
                yt_dlp_wrapper.download(SOURCE, max_filesize=5)
            self.assertEqual(list(root.iterdir()), [])
