@echo off
setlocal
set "BOT_DIR=%~dp0."
set "BOT_PYTHON=%BOT_DIR%\.venv\Scripts\python.exe"
if not exist "%BOT_PYTHON%" set "BOT_PYTHON=%BOT_DIR%\..\TelegramSocialMediaDownloaderBot\.venv\Scripts\python.exe"
if not exist "%BOT_PYTHON%" (
    py -3.10 -m venv "%BOT_DIR%\.venv" || exit /b 1
    set "BOT_PYTHON=%BOT_DIR%\.venv\Scripts\python.exe"
    "%BOT_DIR%\.venv\Scripts\python.exe" -m pip install -r "%BOT_DIR%\requirements.txt" || exit /b 1
)
"%BOT_PYTHON%" "%BOT_DIR%\scripts\install_local.py" %*
exit /b %ERRORLEVEL%
