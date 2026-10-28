@echo off
setlocal
cd /d "%~dp0.."
if not exist "toolkit\python.exe" (
  echo Please extract this bundle into the OAS folder containing toolkit.
  pause
  exit /b 1
)
"toolkit\python.exe" "deploy\ocr_installer.py"
if errorlevel 1 (
  echo OCR setup failed. Existing toolkit and account settings were not changed.
  pause
  exit /b 1
)
echo OCR setup complete. Start a new script process to use it.
pause
