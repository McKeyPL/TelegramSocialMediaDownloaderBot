from collections.abc import Collection

from requests.exceptions import RequestException
from telebot.apihelper import ApiHTTPException
from telebot.formatting import escape_markdown
from telebot.types import Message

import yt_dlp_wrapper
from handlers.base import MediaHandler, PostData
from post_handling_policies import DescriptionPolicy, PostHandlingPolicies, SpoilerPolicy
from post_data_sender import PostDataSender
from related_post_resolver import RelatedPostResolver


class PostOrchestrator:
    def __init__(self, post_data_sender: PostDataSender,
                 related_post_resolver: RelatedPostResolver,
                 allowed_chats: Collection[int]):
        self.post_data_sender = post_data_sender
        self.related_post_resolver = related_post_resolver
        self.allowed_chats = allowed_chats

    def process_link_for_handler(self, message: Message, link: str, handler: MediaHandler,
                                 post_handling_policies: PostHandlingPolicies) -> None:
        site_label = handler.SITE_NAME
        link_to_handle = handler.normalize_url(link)

        post_data = handler.handle(link_to_handle)
        if post_data is None:
            print("Can't handle " + site_label + " link: " + str(link))

            post_data = handler.handle_fallback(link_to_handle)
            if post_data is None:
                return

        self._apply_policies(post_data, post_handling_policies)

        self._send_chain_with_fallback(
            message,
            post_data,
            handler,
            link_to_handle,
        )

    def _apply_policies(self, post_data: PostData,
                        post_handling_policies: PostHandlingPolicies) -> None:
        if post_handling_policies.spoiler_policy == SpoilerPolicy.FORCE_SPOILER:
            post_data.spoiler = True
        elif post_handling_policies.spoiler_policy == SpoilerPolicy.FORCE_NO_SPOILER:
            post_data.spoiler = False

        if post_handling_policies.description_policy == DescriptionPolicy.REMOVE_DESCRIPTION:
            post_data.text = ""

    def _send_chain_with_fallback(self, message: Message, post_data: PostData,
                                  handler: MediaHandler, link_to_handle: str) -> None:
        fetched = [post_data]
        try:
            chain = self.related_post_resolver.resolve(
                post_data,
                handler,
                skip_resolution=(
                    message.chat.id in self.allowed_chats
                    and not handler.resolve_related_in_allowed_chats
                ),
                on_fetch=fetched.append,
            )
            msg_to_reply_to = message
            for entry in chain:
                msg_to_reply_to = self.post_data_sender.send(
                    message,
                    entry.post_data,
                    msg_to_reply_to=msg_to_reply_to,
                    caption_suffix=entry.caption_suffix,
                )
        except (RequestException, ApiHTTPException) as exc:
            print(f"Telegram delivery unconfirmed ({type(exc).__name__}).")
            try:
                self.post_data_sender.bot.reply_to(message, escape_markdown(
                    "Telegram did not confirm delivery. Check the chat before requesting the post again."
                ))
            except (RequestException, ApiHTTPException):
                pass
        except Exception as exc:
            print(f"Couldn't send {handler.SITE_NAME} post ({type(exc).__name__}), trying fallback.")

            fallback_post_data = handler.handle_fallback(link_to_handle)
            if fallback_post_data is None:
                return

            self.post_data_sender.send(message, fallback_post_data)
        finally:
            for fetched_post in fetched:
                for filename, media_type in fetched_post.media:
                    if media_type == "video_file":
                        yt_dlp_wrapper.clean_download(filename)
