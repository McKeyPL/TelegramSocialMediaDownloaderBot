import json
import re
import time
import traceback

import file_converter
import file_downloader
from handlers.base import MediaHandler, PostData
from outbound_http import open_public_url, parse_web_url, validate_public_url


class BooruHandler(MediaHandler):
    SITE_NAME = "booru"
    URL_REGEX = r"((http(s)?://)|^| )(www\.)?[a-zA-Z]*booru\.org/.+"
    MAX_API_BYTES = 2 * 1024 * 1024

    CONVERT_WEBM_VIDEO = False

    def handle(self, link: str) -> PostData | None:
        return self.handle_with_options(link)

    def handle_with_options(self, link: str, allow_nsfw: bool = True, spoil_nsfw: bool = True) -> PostData | None:
        try:
            parts = parse_web_url(link, allowed_host=self._is_booru_host, assume_https=True)
            post = re.fullmatch(r"/(?:images/)?([0-9]+)/?", parts.path)
            if not post:
                return None
            domain = parts.hostname
            canonical_domain = domain.removeprefix("www.")
            api_url = f"https://{domain}/api/v1/json/images/{post[1]}"
            with open_public_url(
                api_url,
                allowed_host=lambda host: host.removeprefix("www.") == canonical_domain,
                timeout=(5, 15),
            ) as response:
                body = response.read(self.MAX_API_BYTES + 1, decode_content=True)
            if len(body) > self.MAX_API_BYTES:
                raise ValueError("Booru response is too large")
            result = self._handle_image(json.loads(body)["image"], domain, allow_nsfw, spoil_nsfw)
            if result is not None:
                for url, media_type in result.media:
                    if media_type != "video_file":
                        validate_public_url(url)
            return result
        except Exception as e:
            print(time.strftime("%d.%m.%Y %H:%M:%S", time.localtime()))
            traceback.print_exception(type(e), e, e.__traceback__)
            print("Couldn't get image from url: " + link)
            print()
            return None

    @staticmethod
    def _is_booru_host(host: str) -> bool:
        return re.fullmatch(r"(?:www\.)?[a-z]*booru\.org", host) is not None

    def _handle_image(self, booru_image, domain, allow_nsfw: bool = True, spoil_nsfw: bool = True) -> PostData | None:
        if not allow_nsfw and any(tag in booru_image['tags'] for tag in ["explicit", "grimdark", "grotesque", "questionable"]):
            return PostData(
                site="booru",
                post_type="text",
                post_id=booru_image['id'],
                text=(f"The requested image ({booru_image['id']}) is NSFW. "
                      "If you want to download it use >>? or >>!.\n\n"
                      ">>? will send the image with a spoiler.\n"
                      ">>! will send the image directly."),
            )

        post_data = PostData(
            site="booru",
            post_type="media",
            post_id=booru_image['id'],
            text=booru_image['description'],
            url="https://" + domain + "/" + str(booru_image['id']),
        )

        author = self._check_if_author_known(booru_image['tags'])
        if author:
            post_data.author = author

        match booru_image['format']:
            case "jpg" | "jpeg" | "png" | "svg":
                # to be changed when booru api handles svg better
                # (currently only low quality png is returned)
                # height + width > 8000 is safe margin because tg api has a limit of 10k
                # but some images under 10k where rejected
                if booru_image['height'] + booru_image['width'] > 8000:
                    post_data.media = [
                        [booru_image['representations']['large'], "photo"]]
                else:
                    post_data.media = [
                        [booru_image['representations']['full'], "photo"]]
            case "gif":
                post_data.media = [
                    [booru_image['representations']['full'], "gif"]]
            case "webm":
                self._handle_video(booru_image, post_data)
            case _:
                post_data.post_type = "text"
                post_data.text = "Unknown image format: " + \
                    booru_image['format'] + "\n" + post_data.text

        post_data.spoiler = booru_image['spoilered'] if spoil_nsfw else False

        return post_data

    def _check_if_author_known(self, tags):
        list_of_authors = []
        for tag in tags:
            if tag.startswith("artist:"):
                list_of_authors.append(tag[7:])
        if not list_of_authors:
            return None
        return ', '.join(list_of_authors)

    def _handle_video(self, booru_image, post_data: PostData) -> None:
        if self.CONVERT_WEBM_VIDEO:
            webm_filename = file_downloader.download_video(url=booru_image['representations']['full'],
                                                           site="booru",
                                                           post_id=str(booru_image['id']))

            converted_filename = file_converter.convert_webm_to_mp4(webm_filename) if webm_filename else ""

            if converted_filename:
                post_data.media = [[converted_filename, "video_file"]]
            else:
                print("Couldn't convert webm to mp4: " + webm_filename)
                post_data.media = [
                    [booru_image['representations']['full'], "video"]]
        else:
            post_data.media = [
                [booru_image['representations']['full'], "video"]]
