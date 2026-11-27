@echo off
setlocal
set "BOT_DIR=%~dp0."
set "BOT_PYTHON=%BOT_DIR%\.venv\Scripts\python.exe"
if not exist "%BOT_PYTHON%" set "BOT_PYTHON=%BOT_DIR%\..\TelegramSocialMediaDownloaderBot\.venv\Scripts\python.exe"
if not exist "%BOT_PYTHON%" (
    set "BOT_PYTHON=%BOT_DIR%\.venv\Scripts\python.exe"
    where py >nul 2>&1 && py -3.10 -m venv "%BOT_DIR%\.venv"
    if not exist "%BOT_DIR%\.venv\Scripts\python.exe" (
        python -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 'Python 3.10 or newer required.')" || exit /b 1
        python -m venv "%BOT_DIR%\.venv" || exit /b 1
    )
)
"%BOT_PYTHON%" "%BOT_DIR%\scripts\sync_requirements.py" || exit /b 1
"%BOT_PYTHON%" "%BOT_DIR%\scripts\install_local.py" %*
exit /b %ERRORLEVEL%
