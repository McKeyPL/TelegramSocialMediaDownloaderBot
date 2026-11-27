# Repository instructions

## Branches and commits

- Use `beta` as the integration target. `origin` is the McKeyPL fork and `upstream` is baatochan's original repository. Check the working tree and branch tips before starting. Preserve unrelated local changes.
- Follow [ROADMAP.md](ROADMAP.md) for the current work sequence. Start integration on a local branch from `beta` in a separate worktree. Merge `upstream/master` there to retain the original ancestry while resolving conflicts. Review `upstream/develop` changes individually. `upstream/reddit` is an unfinished handler.
- Make small, coherent local commits with English subjects. Before publication, squash the verified result onto a separate branch from the current `origin/beta`. Run checks again and verify with `git rev-list --count origin/beta..HEAD` that the publication branch contains one commit. Record the exact upstream SHA in the final commit or PR description. Publish only that branch when authorized. The PR into `beta` should add one commit.

## Integration guardrails

- The current `beta` uses top-level handlers and registers Telegram routes in `bot.py`. Upstream uses `handlers/`, `HandlerRegistry`, `PostData`, `PostOrchestrator` and `PostDataSender`. After integration, put service-specific work in its handler and common routing and upload behavior in the shared pipeline.
- Preserve the fork's Mastodon/Misskey support and thread replies (`e448aa4`, `67e3522`, `6cbb72a`), Streamable (`4c36601`), Bluesky (`90ba022`), and YouTube size, duration and missing-uploader handling (`a4d42f3`, `99d3028`). Preserve literal domain dots in the site regexes (`0481a15` in the fork and `df96c80` upstream).
- When changing 9GAG, Booru or shared downloading, validate URL scheme, host and redirect destinations before making outbound requests. Apply the policy to service-provided media URLs as well. Use finite network timeouts and ensure Selenium and temporary-file cleanup runs on failure. Cover the changed behavior with offline regression tests.
- Telegram mode defaults to `public`. For `local`, `run_bot.py` starts the bundled `telegram-bot-api --local` before starting the bot. Keep transport selection and upload limits in the shared Telegram layer. Keep upload, download and duration limits distinct. Follow the roadmap for native packages and platform checks.

## Local setup and configuration

- Install Python packages in a repository-local `.venv` from `requirements.txt`. Python 3.10.7 is the verified Windows development baseline. Use `python -m venv .venv`, then `.venv\Scripts\python.exe -m pip install -r requirements.txt` on Windows or `.venv/bin/python -m pip install -r requirements.txt` on Linux/macOS. VS Code already points at the Windows venv.
- The Windows installer script can attempt to install FFmpeg and modify the user PATH. Use the direct venv install when FFmpeg is already available. Media processing requires standalone FFmpeg on PATH in addition to the Python wrapper. The optional 9GAG Selenium mode needs Firefox and a working WebDriver.
- Use `python run_bot.py --data-dir <runtime-dir>` for both modes. Document new settings in tracked `config.default.txt` and preserve compatibility with existing configs. Keep real `config.txt`, credentials, sessions, logs and downloaded media out of commits, diagnostics and build artifacts.
- Keep the Docker build context allowlist and release package rules intact. Supply configuration at runtime. Build from the native Linux release when the container needs local API mode.

## Verification

- Run the venv interpreter's `-m pip check`, compile the changed Python files, and run `-m unittest discover -s tests -v`. GitHub Actions uses Python 3.10 on Windows, Linux and macOS. For integration regressions use sanitized fixtures, mocks or a loopback API fixture.
- For live checks use a separate test bot and separate runtime directory. Include a video above 50 MB, a media album and a Windows release rollback when verifying local deployments.
- Treat the existing deployment as production. Restart, reconfiguration, Telegram `logOut` and migration require authorization. Report offline and live verification separately.
