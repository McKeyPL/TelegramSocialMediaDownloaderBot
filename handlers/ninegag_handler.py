import json
import re
import time
import traceback
from threading import Event

from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.common.exceptions import WebDriverException

from handlers.base import MediaHandler, PostData
from outbound_http import open_public_url, parse_web_url, validate_public_url


class NineGagHandler(MediaHandler):
    SITE_NAME = "9gag"
    URL_REGEX = r"((http(s)?://)|^| )(www\.)?9gag\.com/.+"
    HOSTS = {"9gag.com", "www.9gag.com"}
    BROWSER_HOSTS = HOSTS | {"challenges.cloudflare.com", "9cache.com"}
    MAX_PAGE_BYTES = 8 * 1024 * 1024
    USER_AGENT = (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    )

    def __init__(self, use_selenium: bool = True):
        self.use_selenium = use_selenium

    def handle(self, link: str) -> PostData | None:
        try:
            link = parse_web_url(
                link, allowed_host=self.HOSTS.__contains__, assume_https=True,
            ).geturl()
            if self.use_selenium:
                page_source = self._handle_url_with_selenium(link)
            else:
                page_source = self._handle_url_with_requests(link)

            soup = BeautifulSoup(page_source, 'html.parser')
            for script in soup.find_all('script', attrs={"type": "text/javascript"}):
                if "window._config = JSON.parse" in script.get_text():
                    json_text = script.get_text()

                    # Remove single backslashes while keeping escaped backslashes.
                    json_text = re.sub(r'\\(?!\\)', '', json_text)
                    json_text = json_text.replace("\\\\", "\\")

                    # Extract only raw JSON from the surrounding JS expression.
                    json_text = json_text.replace(
                        "window._config = JSON.parse(\"", "")
                    json_text = json_text.replace("\");", "")

                    loaded_json = json.loads(json_text)
                    post = self._check_media_type(loaded_json['data']['post'])
                    if post is not None:
                        for url, _ in post.media:
                            validate_public_url(url)
                    return post
        except Exception as e:
            print(time.strftime("%d.%m.%Y %H:%M:%S", time.localtime()))
            traceback.print_exception(type(e), e, e.__traceback__)
            print()
            return None

        # If parsing succeeds but payload is incomplete.
        print(time.strftime("%d.%m.%Y %H:%M:%S", time.localtime()))
        print("9gag returned incomplete json data.")
        return None

    def _handle_url_with_selenium(self, link: str):
        ff_options = webdriver.FirefoxOptions()
        ff_options.add_argument("--headless")
        ff_options.accept_insecure_certs = False
        ff_options.set_capability("webSocketUrl", True)
        ff_options.set_preference("network.proxy.type", 0)
        ff_options.set_preference("network.trr.mode", 5)
        ff_options.set_preference("network.dns.disablePrefetch", True)
        ff_options.set_preference("media.peerconnection.enabled", False)
        browser = webdriver.Firefox(options=ff_options)
        closing = Event()

        def guard(request):
            if closing.is_set():
                return
            try:
                self._guard_browser_request(request)
            except Exception as exc:
                # Firefox can cancel a paused request before the callback answers it.
                cancelled = isinstance(exc, WebDriverException) and (exc.msg or "").startswith("no such request:")
                if not closing.is_set() and not cancelled:
                    raise

        try:
            browser.set_page_load_timeout(25)
            browser.set_script_timeout(10)
            browser.network.add_request_handler("before_request", guard)
            browser.get(link)
            validate_public_url(browser.current_url, allowed_host=self.HOSTS.__contains__)
            return browser.page_source
        finally:
            closing.set()
            browser.quit()

    def _guard_browser_request(self, request):
        try:
            validate_public_url(
                request.url,
                allowed_host=lambda host: host in self.BROWSER_HOSTS or host.endswith(".9cache.com"),
            )
        except ValueError:
            request.fail()
        else:
            request.continue_request()

    def _handle_url_with_requests(self, link: str):
        with open_public_url(
            link, allowed_host=self.HOSTS.__contains__, timeout=(8, 25), user_agent=self.USER_AGENT,
        ) as response:
            body = response.read(self.MAX_PAGE_BYTES + 1, decode_content=True)
        if len(body) > self.MAX_PAGE_BYTES:
            raise ValueError("9GAG response is too large")
        return body.decode("utf-8")

    def _check_media_type(self, post_json_data) -> PostData | None:
        match post_json_data['type']:
            case "Photo":
                return self._handle_picture(post_json_data)
            case "Animated":
                return self._handle_video(post_json_data)
            case _:
                print("Unsupported 9GAG post type")
                return None

    def _handle_picture(self, post_json_data) -> PostData | None:
        post_data = PostData(
            site="9gag",
            post_type="media",
            post_id=post_json_data['id'],
            url=post_json_data['url'],
            text=post_json_data['title'],
            spoiler=False,
        )

        if "image700" in post_json_data['images']:
            post_data.media = [
                [post_json_data['images']['image700']['url'], "photo"]]
            return post_data
        if "image460" in post_json_data['images']:
            post_data.media = [
                [post_json_data['images']['image460']['url'], "photo"]]
            return post_data

        return None

    def _handle_video(self, post_json_data) -> PostData | None:
        post_data = PostData(
            site="9gag",
            post_type="media",
            post_id=post_json_data['id'],
            url=post_json_data['url'],
            text=post_json_data['title'],
            spoiler=False,
        )

        if "image700sv" in post_json_data['images']:
            video_type = "video"
            if post_json_data['images']['image700sv']['hasAudio'] == 0 and post_json_data['images']['image700sv']['duration'] <= 20:
                video_type = "gif"
            post_data.media = [
                [post_json_data['images']['image700sv']['url'], video_type]]
            return post_data

        if "image460sv" in post_json_data['images']:
            video_type = "video"
            if post_json_data['images']['image460sv']['hasAudio'] == 0 and post_json_data['images']['image460sv']['duration'] <= 20:
                video_type = "gif"
            post_data.media = [
                [post_json_data['images']['image460sv']['url'], video_type]]
            return post_data

        return None
