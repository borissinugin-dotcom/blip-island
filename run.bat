@echo off
cd /d "%~dp0"

set "PY="
set "PYW="

if exist "C:\Python314\python.exe" set "PY=C:\Python314\python.exe"
if exist "C:\Python314\pythonw.exe" set "PYW=C:\Python314\pythonw.exe"

if not defined PY (
    for /f "delims=" %%I in ('where python 2^>nul') do (
        echo "%%I" | findstr /i "WindowsApps" >nul || (
            set "PY=%%I"
            goto :found_py
        )
    )
)
:found_py

if not defined PY (
    echo [Blip] Python not found. Installing Python 3.14 automatically...
    where winget >nul 2>&1
    if %errorlevel%==0 (
        winget install -e --id Python.Python.3.14 --silent --accept-package-agreements --accept-source-agreements
    )
    if exist "%LOCALAPPDATA%\Programs\Python\Python314\python.exe" (
        set "PY=%LOCALAPPDATA%\Programs\Python\Python314\python.exe"
        set "PYW=%LOCALAPPDATA%\Programs\Python\Python314\pythonw.exe"
    ) else if exist "C:\Python314\python.exe" (
        set "PY=C:\Python314\python.exe"
        set "PYW=C:\Python314\pythonw.exe"
    ) else (
        echo [Blip] Downloading official Python 3.14 installer...
        powershell -NoProfile -ExecutionPolicy Bypass -Command "[Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -Uri 'https://www.python.org/ftp/python/3.14.0/python-3.14.0-amd64.exe' -OutFile '%TEMP%\py314.exe'"
        if exist "%TEMP%\py314.exe" (
            "%TEMP%\py314.exe" /quiet InstallAllUsers=0 PrependPath=1 Include_pip=1 Include_test=0
            del /f /q "%TEMP%\py314.exe" >nul 2>&1
        )
        if exist "%LOCALAPPDATA%\Programs\Python\Python314\python.exe" (
            set "PY=%LOCALAPPDATA%\Programs\Python\Python314\python.exe"
            set "PYW=%LOCALAPPDATA%\Programs\Python\Python314\pythonw.exe"
        )
    )
)

if not defined PY (
    echo [Blip] Failed to install Python automatically. Please install Python 3.12+ from python.org.
    pause
    exit /b 1
)

if not defined PYW (
    for %%D in ("%PY%") do (
        if exist "%%~dpDpythonw.exe" set "PYW=%%~dpDpythonw.exe"
    )
)

"%PY%" -c "import PyQt6, httpx, psutil, PIL, numpy, cryptography" >nul 2>&1
if not %errorlevel%==0 (
    echo [Blip] Installing missing libraries from requirements.txt...
    "%PY%" -m pip install --upgrade pip >nul 2>&1
    "%PY%" -m pip install -r "%~dp0requirements.txt"
    if not %errorlevel%==0 (
        echo [Blip] Failed to install required libraries.
        pause
        exit /b 1
    )
)

if defined PYW (
    start "" "%PYW%" "%~dp0main.py" --replace
    exit /b 0
)

"%PY%" "%~dp0main.py" --replace
exit /b %errorlevel%
