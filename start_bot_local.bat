@echo off
setlocal
call "%~dp0start_bot.bat" --data-dir "%~dp0local-data" %*
exit /b %ERRORLEVEL%
