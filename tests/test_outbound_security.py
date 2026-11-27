import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from selenium.common.exceptions import TimeoutException, WebDriverException
from urllib3.exceptions import ProtocolError

import file_downloader
import outbound_http
from handlers.booru_handler import BooruHandler
from handlers.ninegag_handler import NineGagHandler


def dns_result(address):
    return (None, None, None, None, (address, 443))


def http_response(status=200, location=None, body=b""):
    headers = {"Location": location} if location else {}
    response = Mock(status=status, headers=headers)
    response.read.return_value = body
    return response


class OutboundSecurityTests(unittest.TestCase):
    def setUp(self):
        self.dns = self.enter_patch("outbound_http.socket.getaddrinfo", return_value=[dns_result("93.184.215.14")])
        self.https = self.enter_patch("outbound_http.urllib3.HTTPSConnectionPool")
        self.enter_patch("outbound_http.urllib3.HTTPConnectionPool")
        self.enter_patch("builtins.print")
        self.enter_patch("traceback.print_exception")
        self.pool = self.https.return_value

    def enter_patch(self, target, **kwargs):
        patcher = patch(target, **kwargs)
        self.addCleanup(patcher.stop)
        return patcher.start()

    def test_validates_and_pins_each_redirect_hop(self):
        first = http_response(302, "/another-path")
        second = http_response(307, "https://cdn.example/clip.mp4")
        final = http_response()
        self.pool.urlopen.side_effect = [first, second, final]
        self.dns.side_effect = [
            [dns_result("93.184.215.14")],
            [dns_result("93.184.215.15")],
            [dns_result("93.184.215.16")],
        ]

        with outbound_http.open_public_url("https://media.example/clip.mp4") as response:
            self.assertIs(response, final)

        self.assertEqual([call.args[0] for call in self.https.call_args_list], [
            "93.184.215.14", "93.184.215.15", "93.184.215.16",
        ])
        self.assertEqual(self.https.call_args.kwargs["server_hostname"], "cdn.example")
        self.assertEqual(self.https.call_args.kwargs["assert_hostname"], "cdn.example")
        self.assertEqual(self.pool.urlopen.call_args.kwargs["headers"]["Host"], "cdn.example")
        self.assertFalse(self.pool.urlopen.call_args.kwargs["redirect"])
        self.assertEqual(self.pool.urlopen.call_args_list[1].args, ("GET", "/another-path"))
        for response in (first, second, final):
            response.close.assert_called_once()

    def test_rejects_redirect_before_contacting_non_public_destination(self):
        response = http_response(302, "https://internal.example/media")
        self.pool.urlopen.return_value = response
        self.dns.side_effect = [
            [dns_result("93.184.215.14")],
            [dns_result("93.184.215.14"), dns_result("192.168.1.1")],
        ]
        with self.assertRaises(outbound_http.OutboundRequestError), outbound_http.open_public_url("https://media.example"):
            self.fail("Redirect should have been rejected")
        self.pool.urlopen.assert_called_once()
        response.close.assert_called_once()

    def test_rejects_non_http_redirect_before_dns(self):
        self.pool.urlopen.return_value = http_response(302, "file:///example")
        with self.assertRaises(outbound_http.OutboundRequestError), outbound_http.open_public_url("https://media.example"):
            self.fail("Redirect should have been rejected")
        self.dns.assert_called_once()
        self.pool.urlopen.assert_called_once()

    def test_stops_redirect_loop(self):
        self.pool.urlopen.return_value = http_response(302, "/again")
        with self.assertRaisesRegex(outbound_http.OutboundRequestError, "Too many redirects"):
            with outbound_http.open_public_url("https://media.example"):
                self.fail("Redirect loop should have been rejected")
        self.assertEqual(self.pool.urlopen.call_count, outbound_http.MAX_REDIRECTS + 1)

    def test_downloader_cleans_up_after_interrupted_transfer(self):
        response = http_response()

        def broken_stream(_):
            yield b"partial"
            raise ProtocolError("interrupted")

        response.stream.side_effect = broken_stream
        self.pool.urlopen.return_value = response
        previous = Path.cwd()
        with tempfile.TemporaryDirectory() as folder:
            try:
                os.chdir(folder)
                self.assertEqual(file_downloader.download_video("https://media.example/clip.mp4", "9gag", "1"), "")
                self.assertEqual(list(Path("temp", "9gag").iterdir()), [])
            finally:
                os.chdir(previous)
        response.close.assert_called_once()

    def test_downloader_separates_media_for_same_post_and_reuses_completed_file(self):
        first = http_response()
        first.stream.return_value = [b"first"]
        second = http_response()
        second.stream.return_value = [b"second"]
        self.pool.urlopen.side_effect = [first, second]
        previous = Path.cwd()
        with tempfile.TemporaryDirectory() as folder:
            try:
                os.chdir(folder)
                first_url = "https://media.example/first.mp4"
                first_path = file_downloader.download_video(first_url, "9gag", "1")
                second_path = file_downloader.download_video("https://media.example/second.mp4", "9gag", "1")
                self.assertNotEqual(first_path, second_path)
                self.assertEqual(Path(first_path).read_bytes(), b"first")
                self.assertEqual(Path(second_path).read_bytes(), b"second")
                self.assertEqual(file_downloader.download_video(first_url, "9gag", "1"), first_path)
            finally:
                os.chdir(previous)
        self.assertEqual(self.pool.urlopen.call_count, 2)

    def test_9gag_and_booru_route_only_literal_domains(self):
        for handler, good, wrong in (
            (NineGagHandler(False), "9gag.com/gag/abc", "9gag-example.com/gag/abc"),
            (BooruHandler(), "derpibooru.org/images/123", "derpibooru-example.org/images/123"),
        ):
            with self.subTest(handler=handler.SITE_NAME):
                self.assertIsNotNone(re.match(handler.URL_REGEX, good))
                self.assertIsNone(re.match(handler.URL_REGEX, wrong))
                self.assertIsNone(handler.handle("https://" + wrong))
        self.dns.assert_not_called()

    def test_9gag_rejects_cross_site_http_redirect(self):
        self.pool.urlopen.return_value = http_response(302, "https://elsewhere.example")
        self.assertIsNone(NineGagHandler(False).handle("https://9gag.com/gag/abc"))
        self.dns.assert_called_once()
        self.pool.urlopen.assert_called_once()

    def test_9gag_reads_valid_photo_post(self):
        image = "https://img.9cache.com/photo.jpg"
        post = {"id": "example", "url": "https://9gag.com/gag/example", "title": "Photo",
                "type": "Photo", "images": {"image700": {"url": image}}}
        script_json = json.dumps(json.dumps({"data": {"post": post}}))
        body = f'<script type="text/javascript">window._config = JSON.parse({script_json});</script>'.encode()
        self.pool.urlopen.return_value = http_response(body=body)
        result = NineGagHandler(False).handle("9gag.com/gag/example")
        self.assertEqual(result.media, [[image, "photo"]])
        self.assertEqual(self.pool.urlopen.call_args.kwargs["timeout"].read_timeout, 25)

    def test_firefox_checks_requests_and_quits_on_navigation_timeout(self):
        firefox = self.enter_patch("handlers.ninegag_handler.webdriver.Firefox")
        browser = firefox.return_value
        allowed = Mock(url="https://9gag.com/gag/example")
        blocked = Mock(url="https://elsewhere.example")

        def navigate(_):
            phase, guard = browser.network.add_request_handler.call_args.args
            self.assertEqual(phase, "before_request")
            guard(allowed)
            guard(blocked)
            raise TimeoutException("timeout")

        browser.get.side_effect = navigate
        self.assertIsNone(NineGagHandler().handle(allowed.url))
        allowed.fail.assert_not_called()
        allowed.continue_request.assert_called_once()
        blocked.fail.assert_called_once()
        blocked.continue_request.assert_not_called()
        self.assertTrue(firefox.call_args.kwargs["options"].capabilities["webSocketUrl"])
        self.assertFalse(firefox.call_args.kwargs["options"].capabilities["acceptInsecureCerts"])
        browser.set_page_load_timeout.assert_called_once_with(25)
        browser.quit.assert_called_once()

    def test_firefox_does_not_navigate_if_interception_cannot_start(self):
        browser = self.enter_patch("handlers.ninegag_handler.webdriver.Firefox").return_value
        browser.network.add_request_handler.side_effect = RuntimeError("BiDi unavailable")
        self.assertIsNone(NineGagHandler().handle("https://9gag.com/gag/example"))
        browser.get.assert_not_called()
        browser.quit.assert_called_once()

    def test_firefox_ignores_cancelled_and_late_requests_during_cleanup(self):
        browser = self.enter_patch("handlers.ninegag_handler.webdriver.Firefox").return_value
        browser.current_url = "https://9gag.com/gag/example"
        browser.page_source = "fixture"
        cancelled = Mock(url=browser.current_url)
        cancelled.continue_request.side_effect = WebDriverException("no such request: cancelled")
        late = Mock(url=browser.current_url)
        browser.get.side_effect = lambda _: browser.network.add_request_handler.call_args.args[1](cancelled)
        browser.quit.side_effect = lambda: browser.network.add_request_handler.call_args.args[1](late)
        self.assertEqual(NineGagHandler()._handle_url_with_selenium(browser.current_url), "fixture")
        cancelled.continue_request.assert_called_once()
        late.continue_request.assert_not_called()

    def test_booru_validates_id_and_redirect_scope(self):
        handler = BooruHandler()
        self.assertIsNone(handler.handle("https://derpibooru.org/images/"))
        self.assertIsNone(handler.handle("https://derpibooru.org/images/example"))
        self.assertIsNone(handler.handle("https://elsewhere.example/derpibooru.org/123"))
        self.pool.urlopen.assert_not_called()

        self.pool.urlopen.return_value = http_response(302, "https://otherbooru.org/api/v1/json/images/123")
        self.assertIsNone(handler.handle("derpibooru.org/images/123"))
        self.pool.urlopen.assert_called_once()
        self.assertEqual(self.pool.urlopen.call_args.args, ("GET", "/api/v1/json/images/123"))

    def test_booru_preserves_nsfw_options_for_valid_photo(self):
        url = "https://cdn.example/photo.jpg"
        image = {
            "id": 123, "tags": ["explicit"], "description": "Photo", "format": "jpg",
            "height": 100, "width": 100, "spoilered": True, "representations": {"full": url},
        }
        self.pool.urlopen.return_value = http_response(body=json.dumps({"image": image}).encode())
        handler = BooruHandler()
        link = "https://derpibooru.org/123"
        self.assertEqual(handler.handle_with_options(link, allow_nsfw=False).post_type, "text")
        self.assertTrue(handler.handle(link).spoiler)
        result = handler.handle_with_options(link, spoil_nsfw=False)
        self.assertFalse(result.spoiler)
        self.assertEqual(result.media, [[url, "photo"]])
        self.assertEqual(self.pool.urlopen.call_args.kwargs["timeout"].read_timeout, 15)

    def test_booru_rejects_media_with_non_public_dns(self):
        media_url = "https://cdn.example/photo.jpg"
        image = {
            "id": 123, "tags": [], "description": "", "format": "gif",
            "spoilered": False, "representations": {"full": media_url},
        }
        self.pool.urlopen.return_value = http_response(body=json.dumps({"image": image}).encode())
        self.dns.side_effect = [[dns_result("93.184.215.14")], [dns_result("127.0.0.1")]]
        self.assertIsNone(BooruHandler().handle("https://derpibooru.org/123"))
        self.pool.urlopen.assert_called_once()
