@echo off
rem Builds the FaceSort installer on this PC (the GitHub release workflow does the same).
rem Needs: Setup.bat run once (for .venv), and Inno Setup 6 installed (https://jrsoftware.org/isinfo.php).
setlocal
cd /d "%~dp0\.."
set "VPY=.venv\Scripts\python.exe"
if not exist "%VPY%" (
  echo Run Setup.bat first.
  pause
  exit /b 1
)
for /f "usebackq delims=" %%v in (`"%VPY%" -c "import re;print(re.search(r'APP_VERSION = \"([^\"]+)\"', open('ui_common.py', encoding='utf-8').read()).group(1))"`) do set "VER=%%v"
echo Building FaceSort %VER% (folder version, for the installer)...
"%VPY%" -m PyInstaller --noconfirm --clean --log-level WARN --onedir --windowed ^
  --name FaceSort --icon icon.ico ^
  --add-data "models;models" --add-data "icon.png;." ^
  --collect-all pillow_heif --collect-binaries onnxruntime --collect-binaries cv2 --collect-binaries rawpy --collect-data imageio_ffmpeg --exclude-module tkinter ^
  --distpath dist --workpath build facesort.py
if errorlevel 1 goto :fail
set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" set "ISCC=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" (
  echo Inno Setup 6 isn't installed - get it from https://jrsoftware.org/isinfo.php and run this again.
  pause
  exit /b 1
)
"%ISCC%" /Q /DAppVersion=%VER% /DSourceDir=..\dist\FaceSort installer\FaceSort.iss
if errorlevel 1 goto :fail
echo.
echo Done: dist\FaceSort-Setup-%VER%.exe
pause
exit /b 0
:fail
echo.
echo Building the installer failed (see above).
pause
exit /b 1
