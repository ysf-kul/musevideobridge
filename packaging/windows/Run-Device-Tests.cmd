@echo off
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0Run-Device-Tests.ps1" %*
set "MVB_DEVICE_TEST_RESULT=%ERRORLEVEL%"
pause
exit /b %MVB_DEVICE_TEST_RESULT%
