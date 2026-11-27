# TelegramSocialMediaDownloaderBot

Telegram bot for downloading and sharing posts and media from supported sites.

## Install and start

Choose the release ZIP for your OS and architecture, then extract it. The available targets are Windows x64, Linux x64 and arm64 (glibc 2.35 or later), and macOS x64 and arm64 (macOS 13 or later). On Linux and macOS, use `unzip` to preserve the executable permission. A release includes the bot and its native Telegram Bot API server in `bin/`. Python 3.10 and FFmpeg on PATH are host prerequisites. The optional 9GAG Selenium mode needs Firefox and compatible WebDriver support. Keep live configuration and application data in a separate writable directory when upgrading across releases.

Create a venv with `python -m venv .venv`. In the commands below, use `python` from that venv (`.venv\Scripts\python.exe` on Windows, `.venv/bin/python` on Linux and macOS).

```sh
python -m pip install -r requirements.txt
python run_bot.py --data-dir /path/to/bot-data --check
python run_bot.py --data-dir /path/to/bot-data
```

Before starting, copy `config.default.txt` into the data directory as `config.txt`, fill in the bot token and set the user or chat allowlists. Run without `--data-dir` to use the release directory itself. The config, Instagram session, temporary media and local API state stay in the data directory. `Ctrl+C` asks the launcher to shut down.

Run `start_bot.bat` on Windows or `sh ./start_bot.sh` on Linux and macOS. The launcher creates a venv if needed and installs Python dependencies on the first run or when `requirements.txt` changes. After updating the checkout, keep your existing `config.txt`. Local mode uses an available native binary or downloads the matching OS and architecture package for the exact checked-out commit from GitHub Releases. It verifies the package and binary checksums and caches the executable. No GitHub login is required for these public releases. Use a tagged version after its platform job publishes the matching release. Ordinary `beta` commits run tests and have no new downloadable package. Python and FFmpeg remain host prerequisites.

| Mode | Processes and uploads |
| --- | --- |
| `public` (default, including when `[telegram]` is absent) | The launcher runs the bot against Telegram's public API. The default video and animation upload cap is 50 MB. |
| `local` | Set `[telegram] mode = local`. The launcher starts the bundled `telegram-bot-api --local` on `127.0.0.1`, waits for it, then starts the bot. The bot sends files by URI on the shared filesystem, with a default cap of 1999 MB for video and animation. |

Both modes cap photo uploads at 10 MB. Telegram may apply additional restrictions by media type. `max_upload_mb` lowers the mode and method limit. `[downloads] max_size_mb` and `max_duration_minutes` manage download limits separately. With those fields blank, public YouTube and Streamable retain their 123 MiB and 100 MiB download limits and a 20-minute duration limit. Local mode defaults to a 1999 MB download limit and retains those duration defaults. It ignores yt-dlp size estimates and checks the bytes downloaded and the final file size. See `config.default.txt` for operation and timeout settings. [Telegram Bot API limits](https://core.telegram.org/bots/api#sending-files) · [Local Bot API](https://core.telegram.org/bots/api#using-a-local-bot-api-server)

Local mode also needs `TELEGRAM_API_ID` and `TELEGRAM_API_HASH` from your Telegram application, or the corresponding settings in the runtime `config.txt`. The launcher finds the bundled executable automatically. For development from a source checkout, point `local_binary` at a compatible native build or put it on PATH. Keep both processes on the same filesystem so the built-in local upload path works. [Telegram application credentials](https://core.telegram.org/api/obtaining_api_id) · [Native build instructions for Windows, Linux and macOS](https://tdlib.github.io/telegram-bot-api/build.html)

## Run with Docker

A version-tag build publishes separate images to GitHub Container Registry. Tag `v1.0.0` produces `ghcr.io/mckeypl/telegramsocialmediadownloaderbot:v1.0.0-linux-x64` and `ghcr.io/mckeypl/telegramsocialmediadownloaderbot:v1.0.0-linux-arm64`. After the first publish, set the package visibility to **Public** in GitHub Packages if the image should pull without signing in. For Portainer on Docker Standalone, upload [compose.yaml](compose.yaml). Set `BOT_IMAGE` to override the image tag or architecture and `BOT_DATA_DIR` to the path of the host directory with `config.txt`. The default host directory is `/srv/telegram-bot`.

For an offline import, download the matching Docker artifact from the version's GitHub Actions run. Extract it, verify the included SHA-256, then import the image. Use the image name and tag printed by `docker load`.

```powershell
docker load -i .\image-linux-x64.tar.gz
docker run -d --name telegram-downloader --restart unless-stopped `
  --mount "type=bind,source=D:\TelegramBotData,target=/data" `
  IMAGE_NAME:TAG
```

Create `D:\TelegramBotData` and place `config.txt` inside it before starting. On Linux, use a host path such as `/srv/telegram-bot` as the mount source. The mounted directory preserves configuration, sessions and Bot API state across container replacements. For an update, stop and remove the old container and start the new image with the same mount. Local mode uses the bundled Bot API on loopback without publishing an API port to the host.

## Build a Windows package locally

Install Git, Python 3.10 and Visual Studio 2022 Build Tools with **Desktop development with C++**, the Windows SDK and **C++ CMake tools for Windows**. Run the following from a Git checkout. The script fetches the pinned Bot API source and vcpkg dependencies, builds and checks the native binary, and writes a release ZIP plus its SHA-256 to `dist`. The ZIP uses the committed source at `HEAD`. The Windows GitHub Actions runner calls the same script.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\build_bot_api_windows.ps1
.\install_local.bat --archive .\dist\<windows-release.zip> --sha256 <zip-sha256>
```

The installer uses a separate configuration under `local-data\config.txt`. Start it with `start_bot_local.bat` after completing the mode migration below.

## Move an existing bot between modes

1. Stop the current bot and keep its runtime data. Check that no instance with the same token is polling.
2. With the **current** mode in `config.txt`, run `python run_bot.py --data-dir /path/to/bot-data --log-out`. In local mode this starts only the bundled API for the logout and shuts it down afterwards.
3. Set the destination mode, run `--check`, then start the bot. Verify that it receives updates and sends media using a test chat.

Telegram enforces a 10-minute window after logging out from public Bot API during which the bot cannot log back into the public API. Account for that interval when returning to public mode. Changing the installed release and changing Telegram mode are separate operations. [Telegram logOut](https://core.telegram.org/bots/api#logout) · [Telegram Bot API migration notes](https://github.com/tdlib/telegram-bot-api#moving-a-bot-to-a-local-server)

## Validation and Windows deployment

Run the offline checks with `python -m pip check` and `python -m unittest discover -s tests -v`. Pushes and PRs to `beta` or `master` run those checks in the platform matrix. To publish a version, tag its tested commit and push the tag, for example `git tag v1.2.0` followed by `git push origin v1.2.0`. In the tag run, each platform runs its tests before compiling the pinned Bot API, verifying it and publishing that platform's package. Linux x64 and ARM64 also produce Docker image artifacts. A failed platform does not prevent the others from publishing. The release ZIP manifest records the app and native server revisions and file checksums.

On Windows, choose a new managed root and place its config in `<root>\data\config.txt`. Make sure FFmpeg and any optional browser dependencies are available to the account running the bot. For an existing unmanaged installation, first identify its service or scheduled task and stop it separately before cutover. The deployment command manages only processes it starts.

```powershell
python scripts/deploy_windows.py --root D:\BotManaged deploy path\to\release.zip --sha256 <archive-sha256>
python scripts/deploy_windows.py --root D:\BotManaged status
python scripts/deploy_windows.py --root D:\BotManaged rollback
```

Use the SHA-256 recorded by the trusted CI job. Each release gets its own venv and code directory. Config and state stay under `data`. The deploy command waits for polling readiness, and it restarts the prior managed release if the new one fails to start. Use `stop` and `start` to operate the current release. The default deployment timeouts are 180 seconds for readiness and 120 seconds for shutdown. Raise them with `--ready-timeout` and `--stop-timeout` before the subcommand if your launcher has larger limits.

Before promoting a release, use a separate test bot to verify live polling and a small upload in public mode, then a video over 50 MB and an album in local mode. Use a data directory with spaces in its name to exercise file URI handling. For Windows deployment, also exercise update and rollback with that test installation. Verify browser-based 9GAG, Booru and disk usage with representative media.
