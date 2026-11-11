@echo off
rem Starts FaceSort from source (used if you don't want, or couldn't build, the .exe).
cd /d "%~dp0"
if exist "FaceSort.exe" (
  start "" "FaceSort.exe"
  exit /b 0
)
if not exist ".venv\Scripts\pythonw.exe" (
  echo Run Setup.bat first.
  pause
  exit /b 1
)
start "" ".venv\Scripts\pythonw.exe" "facesort.py"
