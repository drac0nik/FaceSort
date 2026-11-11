@echo off
setlocal EnableExtensions
title FaceSort Setup
cd /d "%~dp0"

echo.
echo  ==========================================================
echo     FaceSort setup
echo     Installs everything it needs and builds FaceSort.exe.
echo     First run takes about 5 minutes. Nothing is uploaded.
echo  ==========================================================
echo.

if not exist "facesort.py" goto :notextracted
if not exist "models\sface_dynamic.onnx" goto :notextracted
rem model files from version 1.0 (replaced by the GPU-capable ones)
del /q "models\face_detection_yunet_2023mar.onnx" "models\face_recognition_sface_2021dec.onnx" >nul 2>nul

rem ---------------------------------------------------------------- 1. Python
set "PY="
call :findpy
if defined PY goto :havepy
call :installpy
call :findpy
if not defined PY goto :nopython
:havepy
echo [1/4] Using Python: %PY%

rem ---------------------------------------------------------------- 2. private environment
if exist ".venv\Scripts\python.exe" goto :haveenv
echo [2/4] Creating a private Python environment...
"%PY%" -m venv .venv
if errorlevel 1 goto :fail
:haveenv
set "VPY=%CD%\.venv\Scripts\python.exe"

echo [3/4] Downloading components (Qt, OpenCV, GPU runtime...). This can take a few minutes...
"%VPY%" -m pip install --disable-pip-version-check -q --upgrade pip
"%VPY%" -m pip install --disable-pip-version-check -r requirements.txt pyinstaller
if errorlevel 1 goto :fail

rem ---------------------------------------------------------------- 3. check, then build the exe
echo [4/4] Checking FaceSort with its test suite (about a minute)...
"%VPY%" -m pip install --disable-pip-version-check -q pytest
call :runtests
if errorlevel 1 goto :testsfailed
:build
echo       Building FaceSort.exe (1-3 minutes)...
"%VPY%" -m PyInstaller --noconfirm --clean --log-level WARN --onefile --windowed ^
  --name FaceSort --icon icon.ico ^
  --add-data "models;models" --add-data "icon.png;." ^
  --collect-all pillow_heif --collect-binaries onnxruntime --collect-binaries cv2 --collect-binaries rawpy --collect-data imageio_ffmpeg --exclude-module tkinter ^
  --distpath dist --workpath build facesort.py
if errorlevel 1 goto :buildfail
if not exist "dist\FaceSort.exe" goto :buildfail
rem the old FaceSort.exe can't be replaced while it's running: close it first
tasklist /fi "imagename eq FaceSort.exe" 2>nul | find /i "FaceSort.exe" >nul
if errorlevel 1 goto :copyexe
echo  FaceSort is still open - closing it so it can be updated...
taskkill /im FaceSort.exe >nul 2>nul
timeout /t 5 >nul
tasklist /fi "imagename eq FaceSort.exe" 2>nul | find /i "FaceSort.exe" >nul
if errorlevel 1 goto :copyexe
echo.
echo  FaceSort is still running. Close it (check the taskbar), then press a key.
pause
taskkill /im FaceSort.exe /f >nul 2>nul
timeout /t 2 >nul
:copyexe
copy /y "dist\FaceSort.exe" "FaceSort.exe" >nul
if errorlevel 1 goto :copyfail
rmdir /s /q build >nul 2>nul
rmdir /s /q dist >nul 2>nul
del /q FaceSort.spec >nul 2>nul

rem ---------------------------------------------------------------- 4. shortcut + start
set "FS_DIR=%CD%"
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$d=[Environment]::GetFolderPath('Desktop'); $s=(New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path $d 'FaceSort.lnk')); $s.TargetPath=(Join-Path $env:FS_DIR 'FaceSort.exe'); $s.WorkingDirectory=$env:FS_DIR; $s.IconLocation=(Join-Path $env:FS_DIR 'FaceSort.exe')+',0'; $s.Save()" >nul 2>nul

echo.
echo  Done! FaceSort.exe is ready in this folder, and there is a
echo  FaceSort shortcut on your desktop. Starting it now...
echo  (The first start of the .exe takes a few seconds.)
echo.
echo  FaceSort.exe works on its own: you can move it anywhere, and you
echo  can delete the ".venv" folder here to free up space.
echo.
start "" "%CD%\FaceSort.exe"
timeout /t 8 >nul
exit /b 0

rem ================================================================= helpers
:runtests
set "QT_QPA_PLATFORM=offscreen"
"%VPY%" -m pytest -q -x --tb=short
set "RC=%ERRORLEVEL%"
set "QT_QPA_PLATFORM="
exit /b %RC%

:findpy
for %%v in (3.12 3.13 3.11 3.10) do (
  if not defined PY (
    for /f "delims=" %%i in ('py -%%v -c "import sys;print(sys.executable)" 2^>nul') do set "PY=%%i"
  )
)
if defined PY exit /b 0
for /f "delims=" %%i in ('python -c "import sys;print(sys.executable if (3,10)<=sys.version_info[:2]<=(3,13) else str())" 2^>nul') do set "PY=%%i"
if defined PY exit /b 0
if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
exit /b 0

:installpy
echo Python 3.10-3.13 was not found. Installing Python 3.12 for your user account...
where winget >nul 2>nul
if errorlevel 1 goto :dlpython
winget install -e --id Python.Python.3.12 --scope user --silent --accept-package-agreements --accept-source-agreements
if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" exit /b 0
:dlpython
echo Downloading the Python installer from python.org...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ProgressPreference='SilentlyContinue'; [Net.ServicePointManager]::SecurityProtocol='Tls12'; Invoke-WebRequest 'https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe' -OutFile (Join-Path $env:TEMP 'facesort-python.exe')"
if not exist "%TEMP%\facesort-python.exe" exit /b 1
echo Installing Python (about a minute)...
"%TEMP%\facesort-python.exe" /quiet InstallAllUsers=0 PrependPath=1 Include_launcher=1 Include_test=0
del /q "%TEMP%\facesort-python.exe" >nul 2>nul
exit /b 0

rem ================================================================= problems
:testsfailed
echo.
echo  One of FaceSort's self-tests failed (details above). Your library and photos
echo  were not touched - the tests only use a small made-up library.
echo  Your current FaceSort.exe is still there. Build the new version anyway?
choice /c YN /m "  Build anyway"
if errorlevel 2 (
  echo  Not built. Please send the lines above to get it fixed.
  pause
  exit /b 1
)
goto :build

:buildfail
echo.
echo  Building the .exe didn't work, but FaceSort can still run.
echo  Starting it with "Run FaceSort.bat" instead...
start "" "%CD%\Run FaceSort.bat"
pause
exit /b 1

:copyfail
echo.
echo  The new FaceSort.exe was built, but the old one couldn't be replaced
echo  (it is probably still open). Close FaceSort and run Setup.bat again.
pause
exit /b 1

:nopython
echo.
echo  Couldn't install Python automatically.
echo  Please install Python 3.12 from https://www.python.org/downloads/windows/
echo  (tick "Add python.exe to PATH"), then run Setup.bat again.
pause
exit /b 1

:notextracted
echo.
echo  Please extract (unzip) the whole FaceSort folder first, then run Setup.bat
echo  from the extracted folder. Right-click the zip and choose "Extract All...".
pause
exit /b 1

:fail
echo.
echo  Setup failed. Check your internet connection and run Setup.bat again.
echo  If it keeps failing, delete the ".venv" folder here and try once more.
pause
exit /b 1
