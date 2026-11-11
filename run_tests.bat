@echo off
rem Runs FaceSort's test suite (a small made-up library - your own library and photos
rem are never touched). Setup.bat runs this too, before building.
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Run Setup.bat first.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q pytest pyflakes
set "QT_QPA_PLATFORM=offscreen"
".venv\Scripts\python.exe" -m pytest %*
echo.
pause
