@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"

set "NO_PAUSE=0"
if /i "%~1"=="--no-pause" set "NO_PAUSE=1"

echo ==================================================
echo   Dynamic Island Borh-edition: Установка библиотек
echo ==================================================
echo.

set "PY_EXE="
set "PY_ARGS="

if exist "%~dp0.venv\Scripts\python.exe" set "PY_EXE=%~dp0.venv\Scripts\python.exe"
if not defined PY_EXE if exist "C:\Python314\python.exe" set "PY_EXE=C:\Python314\python.exe"
if not defined PY_EXE (
    for /f "delims=" %%I in ('where python 2^>nul') do (
        echo "%%I" | findstr /i "WindowsApps" >nul || (
            set "PY_EXE=%%I"
            goto :found_py
        )
    )
)
if not defined PY_EXE (
    where py >nul 2>&1 && (
        set "PY_EXE=py"
        set "PY_ARGS=-3"
    )
)

:found_py
if not defined PY_EXE goto :no_python

echo Найден Python: %PY_EXE% %PY_ARGS%
"%PY_EXE%" %PY_ARGS% --version
if errorlevel 1 goto :err_exit

"%PY_EXE%" %PY_ARGS% -m pip --version >nul 2>&1
if errorlevel 1 "%PY_EXE%" %PY_ARGS% -m ensurepip --upgrade

echo.
echo [1/3] Обновление pip и установка основных библиотек...
"%PY_EXE%" %PY_ARGS% -m pip install --upgrade pip --disable-pip-version-check

if exist "%~dp0requirements.txt" (
    "%PY_EXE%" %PY_ARGS% -m pip install --upgrade --prefer-binary -r "%~dp0requirements.txt"
) else (
    "%PY_EXE%" %PY_ARGS% -m pip install --upgrade --prefer-binary "PyQt6>=6.8" "httpx>=0.27" "psutil>=6.0" "pillow>=10.0" "numpy>=1.26"
)
if errorlevel 1 goto :err_core

echo.
echo [2/3] Установка дополнительных модулей (PDF / быстрый QR)...
for %%P in ("pypdf>=4.0" "zxing-cpp>=2.2" "opencv-python>=4.9") do (
    "%PY_EXE%" %PY_ARGS% -m pip install --prefer-binary -q %%~P >nul 2>&1 || echo   [пропущено] %%~P недоступен для текущей версии Python
)

echo.
echo [3/3] Проверка импортов...
"%PY_EXE%" %PY_ARGS% -c "import importlib, importlib.util; req=[('PyQt6.QtCore','PyQt6'),('httpx','httpx'),('psutil','psutil'),('PIL','pillow'),('numpy','numpy')]; opt=[('pypdf','pypdf'),('zxingcpp','zxing-cpp'),('cv2','opencv-python')]; [print('  OK:', n, getattr(importlib.import_module(m), '__version__', 'installed')) for m,n in req]; [print('  OK (opt):', n) if importlib.util.find_spec(m) else print('  -- (opt):', n, '(fallback built-in)') for m,n in opt]"
if errorlevel 1 goto :err_exit

echo.
echo Готово! Все нужные библиотеки установлены. Запускайте программу через run.bat.
goto :done

:no_python
echo [ОШИБКА] Python не найден в .venv, PATH или C:\Python314.
echo Установите Python 3.11+ с https://www.python.org/downloads/ и отметьте Add Python to PATH.
goto :err_exit

:err_core
echo [ОШИБКА] Не удалось установить обязательные библиотеки.
goto :err_exit

:err_exit
if "%NO_PAUSE%"=="0" pause
exit /b 1

:done
if "%NO_PAUSE%"=="0" pause
exit /b 0
