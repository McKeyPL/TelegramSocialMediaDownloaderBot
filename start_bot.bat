@echo off
setlocal
set "BOT_DIR=%~dp0."
set "BOT_PYTHON=%BOT_DIR%\.venv\Scripts\python.exe"
if not exist "%BOT_PYTHON%" set "BOT_PYTHON=%BOT_DIR%\..\TelegramSocialMediaDownloaderBot\.venv\Scripts\python.exe"
if not exist "%BOT_PYTHON%" (
    echo Bot virtual environment not found. 1>&2
    exit /b 1
)
"%BOT_PYTHON%" "%BOT_DIR%\run_bot.py" --data-dir "%BOT_DIR%" %*
exit /b %ERRORLEVEL%
