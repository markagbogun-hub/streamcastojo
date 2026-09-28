@echo off
setlocal EnableExtensions EnableDelayedExpansion
title RadioCastOS - Local Windows Build
cd /d "%~dp0"

set "ROOT=%~dp0"
set "PYTHON=python"
set "FFMPEG_URL=https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
set "FFMPEG_ZIP=%TEMP%\RadioCastOS-ffmpeg.zip"
set "FFMPEG_DIR=%TEMP%\RadioCastOS-ffmpeg"
set "ISCC="

call :header

REM ============================================================
REM 1. Check Python
REM ============================================================
where %PYTHON% >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Python was not found on PATH.
    echo Install Python 3.11 from https://www.python.org/downloads/windows/
    goto :fail
)

%PYTHON% --version
if errorlevel 1 goto :fail

echo [OK] Python found.

REM ============================================================
REM 2. Install dependencies
REM ============================================================
echo.
echo [1/6] Installing Python build dependencies...
%PYTHON% -m pip install --upgrade pip
if errorlevel 1 goto :fail
%PYTHON% -m pip install -r requirements.txt
if errorlevel 1 goto :fail
%PYTHON% -m PyInstaller --version
if errorlevel 1 goto :fail

echo [OK] Dependencies installed.

REM ============================================================
REM 3. Validate source
REM ============================================================
echo.
echo [2/6] Validating Python source...
%PYTHON% -m compileall -q src
if errorlevel 1 (
    echo [ERROR] Python source compilation failed.
    goto :fail
)

%PYTHON% -c "import sys; sys.path.insert(0,'src'); import main; import app_gui; import scheduler; import player; import streamer; import recorder; import devices; import mixer; import library; import asrun; import shoutcast_v1; print('Source imports OK')"
if errorlevel 1 (
    echo [ERROR] Source import validation failed.
    goto :fail
)

echo [OK] Source validation passed.

REM ============================================================
REM 4. Download/stage FFmpeg automatically if needed
REM ============================================================
echo.
echo [3/6] Checking FFmpeg runtime...
if exist "assets\ffmpeg.exe" if exist "assets\ffplay.exe" goto ffmpeg_ok

echo FFmpeg binaries are missing. Downloading the Windows essentials build...
if exist "%FFMPEG_ZIP%" del /f /q "%FFMPEG_ZIP%"
if exist "%FFMPEG_DIR%" rmdir /s /q "%FFMPEG_DIR%"

powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; Invoke-WebRequest -Uri '%FFMPEG_URL%' -OutFile '%FFMPEG_ZIP%'; Expand-Archive -Path '%FFMPEG_ZIP%' -DestinationPath '%FFMPEG_DIR%' -Force"
if errorlevel 1 (
    echo [ERROR] FFmpeg download/extraction failed.
    goto :fail
)

if not exist "assets" mkdir "assets"

for /r "%FFMPEG_DIR%" %%F in (ffmpeg.exe) do if not exist "assets\ffmpeg.exe" copy /y "%%F" "assets\ffmpeg.exe" >nul
for /r "%FFMPEG_DIR%" %%F in (ffplay.exe) do if not exist "assets\ffplay.exe" copy /y "%%F" "assets\ffplay.exe" >nul

if not exist "assets\ffmpeg.exe" (
    echo [ERROR] ffmpeg.exe was not found after extraction.
    goto :fail
)
if not exist "assets\ffplay.exe" (
    echo [ERROR] ffplay.exe was not found after extraction.
    goto :fail
)

:ffmpeg_ok
echo [OK] FFmpeg runtime is ready.

REM ============================================================
REM 5. Clean and build PyInstaller package
REM ============================================================
echo.
echo [4/6] Cleaning previous build output...
if exist "build" rmdir /s /q "build"
if exist "dist" rmdir /s /q "dist"
if exist "Output" rmdir /s /q "Output"

if exist "build" (
    echo [ERROR] Could not remove build folder.
    goto :fail
)
if exist "dist" (
    echo [ERROR] Could not remove dist folder.
    goto :fail
)

echo.
echo [5/6] Building RadioCastOS with PyInstaller...
%PYTHON% -m PyInstaller --noconfirm --clean build.spec
if errorlevel 1 (
    echo [ERROR] PyInstaller build failed.
    goto :fail
)

if not exist "dist\RadioCastOS\RadioCastOS.exe" (
    echo [ERROR] RadioCastOS.exe was not created.
    goto :fail
)
if not exist "dist\RadioCastOS\ffmpeg.exe" (
    echo [ERROR] Bundled ffmpeg.exe is missing.
    goto :fail
)
if not exist "dist\RadioCastOS\ffplay.exe" (
    echo [ERROR] Bundled ffplay.exe is missing.
    goto :fail
)

echo [OK] Portable application built successfully.

REM ============================================================
REM 6. Build installer if Inno Setup is installed
REM ============================================================
echo.
echo [6/6] Looking for Inno Setup...
if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not defined ISCC if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if not defined ISCC (
    where ISCC.exe >nul 2>nul
    if not errorlevel 1 set "ISCC=ISCC.exe"
)

if not defined ISCC (
    echo [INFO] Inno Setup is not installed.
    echo [INFO] Portable build is still ready at:
    echo        dist\RadioCastOS\RadioCastOS.exe
    goto :success
)

echo [OK] Inno Setup found: !ISCC!
echo Building installer...
"!ISCC!" installer.iss
if errorlevel 1 (
    echo [ERROR] Inno Setup failed.
    goto :fail
)

if not exist "Output\RadioCastOS-Setup.exe" (
    echo [ERROR] Installer was not created.
    goto :fail
)

echo [OK] Installer created: Output\RadioCastOS-Setup.exe

:success
call :header
 echo BUILD COMPLETED SUCCESSFULLY
 echo.
echo Portable EXE:
echo   %ROOT%dist\RadioCastOS\RadioCastOS.exe
if exist "Output\RadioCastOS-Setup.exe" (
    echo Installer:
    echo   %ROOT%Output\RadioCastOS-Setup.exe
)
 echo.
echo You can now test the portable EXE before installing it.
echo.
pause
exit /b 0

:fail
 echo.
 echo ============================================
 echo BUILD FAILED
 echo ============================================
 echo Check the error immediately above this message.
 echo.
pause
exit /b 1

:header
echo ============================================
echo   RadioCastOS - Windows Local Build
echo ============================================
exit /b 0
