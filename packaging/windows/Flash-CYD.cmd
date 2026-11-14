@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Flash-CYD.ps1" %*
set "MVB_FLASH_RESULT=%ERRORLEVEL%"
pause
exit /b %MVB_FLASH_RESULT%
