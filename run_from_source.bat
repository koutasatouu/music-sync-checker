@echo off
setlocal
cd /d "%~dp0"
set "PY=python"
set "PYW=pythonw"
where python >nul 2>nul || (set "PY=py" & set "PYW=pyw")
%PY% --version >nul 2>nul || (
    echo Python was not found. Install it from python.org and tick "Add python.exe to PATH".
    pause & exit /b 1
)
%PY% -c "import mutagen" >nul 2>nul || (
    echo Installing mutagen ^(metadata reader^) - one time only...
    %PY% -m pip install mutagen
)
rem Start without a console window
start "" %PYW% "Music Sync Checker.pyw"
