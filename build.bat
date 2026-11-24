@echo off
setlocal
cd /d "%~dp0"
title Building Music Sync Checker

set "PY=python"
where python >nul 2>nul || set "PY=py"
%PY% --version >nul 2>nul || (
    echo Python was not found. Install it from python.org and tick "Add python.exe to PATH".
    pause & exit /b 1
)

echo.
echo [1/4] Installing build tools (PyInstaller + mutagen metadata reader)...
%PY% -m pip install --upgrade pyinstaller mutagen || goto :fail

echo.
echo [2/4] Running tests...
%PY% -m unittest discover -s tests || echo [WARN] Some tests failed - building anyway.

echo.
echo [3/4] Building the app...
%PY% -m PyInstaller --noconfirm --clean --windowed --onedir ^
    --name "MusicSyncChecker" ^
    --icon "icon.ico" ^
    --splash "splash.png" ^
    --add-data "icon.ico;." --add-data "icon.png;." ^
    --collect-submodules mutagen ^
    "music_sync_checker.py" || goto :fail

echo.
echo [4/4] Creating Start Menu and Desktop shortcuts...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0create_shortcut.ps1" ^
    -Target "%~dp0dist\MusicSyncChecker\MusicSyncChecker.exe" || goto :fail

echo.
echo ============================================================
echo  Done!  Open Start, type "Music Sync Checker",
echo  right-click it and choose "Pin to Start" (or "Pin to taskbar").
echo ============================================================
start "" "dist\MusicSyncChecker\MusicSyncChecker.exe"
pause
exit /b 0

:fail
echo.
echo Build failed - scroll up to see the error.
pause
exit /b 1
