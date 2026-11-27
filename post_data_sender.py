import re
import time
from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory

from telebot.formatting import escape_markdown
from telebot.types import (InputMediaPhoto, InputMediaVideo,
                           LinkPreviewOptions, ReplyParameters)

import file_downloader
import yt_dlp_wrapper
from handlers.base import PostData
from media_policy import MediaLimitError, MediaPolicy


class Caption:
    def __init__(self, short, long):
        self.short = short
        self.long = long


class PostDataSender:
    def __init__(self, bot, error_message: str, *, policy: MediaPolicy | None = None):
        self.bot = bot
        self.error_message = error_message
        self.policy = policy or MediaPolicy()

    def send(self, orig_tg_msg, post_data: PostData, msg_to_reply_to=None, caption_suffix=""):
        caption = self.prepare_caption(post_data)
        if msg_to_reply_to is None:
            msg_to_reply_to = orig_tg_msg

        if caption_suffix:
            caption.long += caption_suffix

        match (post_data.post_type):
            case "media":
                return self.send_media_post(orig_tg_msg, post_data, caption, msg_to_reply_to)
            case "text":
                return self.send_text_post(orig_tg_msg, caption, msg_to_reply_to)
            case _:
                return self.bot.reply_to(msg_to_reply_to, self.error_message)

    def prepare_caption(self, post_data: PostData):
        long_caption = ""
        short_caption = ""
        if post_data.text:
            post_data.text = self.remove_hashtags(post_data.text)
            long_caption += post_data.text
        if post_data.author:
            long_caption += "\n\nby: " + post_data.author
            short_caption += "by: " + post_data.author
        if post_data.url:
            long_caption += "\n" + post_data.url
            short_caption += "\n" + post_data.url
        long_caption = escape_markdown(long_caption)
        short_caption = escape_markdown(short_caption)

        if post_data.poll:
            long_caption = "*This post is a poll\!*\n\n" + long_caption

        if post_data.site == "twitter" and post_data.community_note:
            long_caption += self.parse_community_notes(post_data)

        return Caption(short_caption, long_caption)

    def remove_hashtags(self, text):
        # Remove hashtags when there are 4 or more grouped together
        # # and eveyrthing not being a whitespace is considered a signle hashtag
        text = re.sub(r'((#[^\s]+)\s+){3,}(#[^\s]+)',
                      '', text, flags=re.UNICODE)
        # Removing hashtags may leave some empty lines so we need to remove them
        text = text.strip()
        return text

    def parse_community_notes(self, post_data: PostData):
        if not post_data.community_note_links:
            return "\n\n*This tweet has community notes*:\n" + escape_markdown(post_data.community_note_text)

        note_arr = []
        split_indices = []
        links = []
        text = post_data.community_note_text

        split_indices.append(0)
        for link in post_data.community_note_links:
            split_indices.append(link['from'])
            split_indices.append(link['to'])
            links.append(link['url'])
        split_indices.append(None)

        link_index = 0
        for i in range(len(split_indices) - 1):
            is_link = i % 2 == 1
            if not is_link:
                note_arr.append(
                    {"text": text[split_indices[i]:split_indices[i+1]], "is_link": is_link})
            else:
                note_arr.append({"text": text[split_indices[i]:split_indices[i+1]],
                                "is_link": is_link, "link": links[link_index]})
                link_index += 1

        note = "\n\n*This tweet has community notes*:\n"
        for note_part in note_arr:
            if note_part["is_link"]:
                note += "[" + escape_markdown(note_part["text"]) + \
                    "](" + note_part["link"] + ")"
            else:
                note += escape_markdown(note_part["text"])

        return note

    def send_media_post(self, orig_tg_msg, post_data: PostData, caption, msg_to_reply_to):
        with ExitStack() as stack:
            for value, media_type in post_data.media:
                if media_type == "video_file":
                    stack.callback(yt_dlp_wrapper.clean_download, value)

            prepared = []
            directory = None
            try:
                for index, (value, media_type) in enumerate(post_data.media):
                    if media_type not in {"photo", "gif", "video", "video_file"}:
                        continue
                    upload_limit = self.policy.upload_limit(media_type)
                    if media_type != "video_file":
                        if directory is None:
                            root = Path("temp")
                            root.mkdir(exist_ok=True)
                            directory = Path(stack.enter_context(TemporaryDirectory(
                                prefix="upload-", dir=root, ignore_cleanup_errors=True,
                            )))
                        value = file_downloader.download_video(
                            value, post_data.site, f"{post_data.post_id}_{index}",
                            directory=directory,
                            max_bytes=min(upload_limit, self.policy.download_limit()),
                        )
                        if not value:
                            raise OSError("Could not download media")
                    path = Path(value).resolve()
                    if not path.is_file() or not 0 < path.stat().st_size <= upload_limit:
                        raise MediaLimitError("Media does not meet the upload size limit")
                    prepared.append((path, media_type))
            except (OSError, MediaLimitError):
                return self.bot.reply_to(msg_to_reply_to, self.error_message)

            if not prepared:
                return self.bot.reply_to(msg_to_reply_to, self.error_message)

            if len(prepared) == 1:
                path, media_type = prepared[0]
                media = self._upload_value(stack, path)
                sent_message = self._send_single_media(
                    orig_tg_msg, media, media_type, caption, post_data.spoiler, msg_to_reply_to,
                )
            else:
                sent_message = self._send_album(
                    orig_tg_msg, prepared, caption, post_data.spoiler, msg_to_reply_to,
                )
        self.delete_handled_message(orig_tg_msg)
        return sent_message

    def _upload_value(self, stack: ExitStack, path: Path):
        if self.policy.local:
            return path.as_uri()
        return stack.enter_context(path.open("rb"))

    def _send_single_media(self, original, media, media_type, caption, spoiler, reply_to):
        # Telegram accepts a longer caption as a separate message.
        long_caption = len(caption.long) > 1024
        if media_type == "photo":
            send, field = self.bot.send_photo, "photo"
        elif media_type == "gif":
            send, field = self.bot.send_animation, "animation"
        else:
            send, field = self.bot.send_video, "video"

        sent = send(
            chat_id=original.chat.id,
            caption=caption.short if long_caption else caption.long,
            has_spoiler=spoiler,
            reply_parameters=ReplyParameters(message_id=reply_to.message_id, allow_sending_without_reply=True),
            timeout=self.policy.timeout,
            **{field: media},
        )
        if long_caption:
            return self._send_caption(original, caption.long, sent)
        return sent

    def _send_album(self, original, prepared, caption, spoiler, reply_to):
        split = len(prepared) > 10
        extra_caption = split or len(caption.long) > 1024
        start = 0
        while start < len(prepared):
            end = min(start + 10, len(prepared))
            if len(prepared) - end == 1:
                end -= 1  # Each Telegram media group needs at least two items.
            with ExitStack() as files:
                group = [
                    (InputMediaPhoto if kind == "photo" else InputMediaVideo)(
                        media=self._upload_value(files, path), has_spoiler=spoiler,
                    )
                    for path, kind in prepared[start:end]
                ]
                group[0].caption = caption.short if extra_caption else caption.long
                sent = self.bot.send_media_group(
                    chat_id=original.chat.id,
                    media=group,
                    reply_parameters=ReplyParameters(message_id=reply_to.message_id, allow_sending_without_reply=True),
                    timeout=self.policy.timeout,
                )
            reply_to = sent[0]
            start = end

        if extra_caption:
            return self._send_caption(original, caption.long, reply_to)
        return reply_to

    def _send_caption(self, original, text, reply_to):
        return self.bot.send_message(
            chat_id=original.chat.id,
            text=text,
            reply_parameters=ReplyParameters(message_id=reply_to.message_id),
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        )

    def send_text_post(self, orig_tg_msg, caption, msg_to_reply_to):
        sent_message = self.bot.send_message(chat_id=orig_tg_msg.chat.id,
                                             text=caption.long,
                                             reply_parameters=ReplyParameters(
                                                 message_id=msg_to_reply_to.message_id,
                                                 allow_sending_without_reply=True),
                                             link_preview_options=LinkPreviewOptions(is_disabled=True))
        self.delete_handled_message(orig_tg_msg)
        return sent_message

    def delete_handled_message(self, message):
        try:
            self.bot.delete_message(message.chat.id, message.id)
        except Exception as e:
            print(time.strftime("%d.%m.%Y %H:%M:%S", time.localtime()))
            print(str(e))
            print("Cant remove message in chat " +
                  str(message.chat.title) + " (" + str(message.chat.id) + ").")
            print()
