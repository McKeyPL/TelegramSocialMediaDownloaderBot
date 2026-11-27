#!/usr/bin/env python3
import configparser
import json
import os
import signal
import sys
import tempfile
from hashlib import sha256
from pathlib import Path
from threading import Event, Thread

import telebot
from requests.exceptions import RequestException
from telebot import apihelper
from telebot.formatting import escape_markdown
from tendo import singleton

from handlers import BooruHandler, HandlerRegistry
from media_policy import MediaPolicy
from post_orchestrator import PostOrchestrator
from post_data_sender import PostDataSender
from related_post_resolver import RelatedPostResolver
from runtime_config import RuntimeSettings, read_config
from telegram_routes import TelegramRoutes


def load_config_or_exit(config_path: str = "config.txt") -> configparser.ConfigParser:
    try:
        return read_config(Path(config_path))
    except FileNotFoundError:
        print("No config file. Create config file and run the script again.")
        sys.exit(1)


def create_bot(config: configparser.ConfigParser) -> telebot.TeleBot:
    settings = RuntimeSettings.from_config(config)
    if settings.mode == "local":
        endpoint = settings.local_endpoint
        if os.environ.get("TSMD_LOCAL_ENDPOINT") != endpoint:
            raise ValueError("Run run_bot.py to start the bot in local mode")
        apihelper.API_URL = endpoint + "/bot{0}/{1}"
        apihelper.FILE_URL = endpoint + "/file/bot{0}/{1}"
    else:
        apihelper.API_URL = apihelper.FILE_URL = None
    bot = telebot.TeleBot(config['config']['token'])
    bot.parse_mode = "MarkdownV2"
    return bot


def register_shutdown_signal_handlers(handler) -> None:
    for signal_name in ("SIGINT", "SIGTERM", "SIGBREAK"):
        shutdown_signal = getattr(signal, signal_name, None)
        if shutdown_signal is not None:
            signal.signal(shutdown_signal, handler)


def run_polling_forever(bot: telebot.TeleBot) -> None:
    stopping = Event()
    stop_marker = os.environ.get("TSMD_BOT_STOP_FILE")

    def request_stop(*_args):
        stopping.set()
        bot.stop_polling()

    def watch_stop_marker():
        while not stopping.is_set():
            if Path(stop_marker).exists():
                request_stop()
                return
            stopping.wait(0.2)

    register_shutdown_signal_handlers(request_stop)
    if stop_marker:
        Thread(target=watch_stop_marker, daemon=True).start()
    try:
        while not stopping.is_set():
            try:
                bot.polling(timeout=15, long_polling_timeout=10, logger_level=0)
            except Exception as exc:
                print(f"Telegram polling failed ({type(exc).__name__}).")
            stopping.wait(1)
    finally:
        request_stop()
        bot.stop_bot()


def main() -> None:
    config = load_config_or_exit()
    media_policy = MediaPolicy.from_config(config)
    # Lock by bot identity even when versions are started from different directories.
    bot_identity = sha256(config["config"]["token"].partition(":")[0].encode()).hexdigest()
    instance_lock = singleton.SingleInstance(
        lockfile=str(Path(tempfile.gettempdir()) / f"telegram-social-bot-{bot_identity}.lock"),
    )
    raw_allowed_users = config['config']['allowed_users'].strip()
    allowed_users = None if raw_allowed_users.upper() == "ALL" else json.loads(raw_allowed_users)
    allowed_chats = json.loads(config['config']['allowed_chats'])

    bot = create_bot(config)
    bot_id = bot.get_me().id

    error_message = escape_markdown(
        "Can't download this post. Try again later.")
    post_data_sender = PostDataSender(bot, error_message, policy=media_policy)
    related_post_resolver = RelatedPostResolver()
    post_orchestrator = PostOrchestrator(
        post_data_sender,
        related_post_resolver,
        allowed_chats,
    )

    handler_registry = HandlerRegistry.create_from_config(config, media_policy=media_policy)
    handlers_to_process = handler_registry.get_active_handlers()
    supported_sites_regex = handler_registry.get_active_combined_regex()

    # Site-specific workflows still need direct access to selected handlers.
    booru_handler = handler_registry.get_handler(BooruHandler.SITE_NAME)

    routes = TelegramRoutes(
        bot,
        config,
        allowed_users,
        allowed_chats,
        bot_id,
        handlers_to_process,
        supported_sites_regex,
        post_orchestrator,
    )
    routes.register_handlers()
    routes.register_special_derpibooru_handler(booru_handler, post_data_sender)

    ready_marker = os.environ.get("TSMD_BOT_READY_FILE")
    if ready_marker:
        # Check that polling is available without acknowledging any pending updates.
        bot.get_updates(limit=1, timeout=5, long_polling_timeout=0)
        Path(ready_marker).touch(exist_ok=False)
    run_polling_forever(bot)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, configparser.Error, RequestException,
            telebot.apihelper.ApiException, singleton.SingleInstanceException) as exc:
        print(f"Telegram bot startup failed ({type(exc).__name__}).", file=sys.stderr)
        sys.exit(1)
