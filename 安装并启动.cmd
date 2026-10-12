@echo off
setlocal
cd /d "%~dp0"
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0deploy\bootstrap.ps1" %*
if errorlevel 1 (
    echo.
    echo Installation or startup failed. See the message above and log\installer.
    pause
    exit /b 1
)
endlocal
