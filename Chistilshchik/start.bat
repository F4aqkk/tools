@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
title Chistilshchik

set "PY="
set "VENV_PY=.venv\Scripts\python.exe"
set "VENV_PYW=.venv\Scripts\pythonw.exe"
set "PY_URL=https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe"
set "PY_SETUP=%TEMP%\python-3.12.10-amd64.exe"

if exist "%VENV_PY%" goto deps

rem ---------- 1. Ищем Python ----------
echo [1/4] Ищу Python...
call :try_cmd py -3
if not defined PY call :try_cmd python
if not defined PY call :try_path "%LocalAppData%\Programs\Python\Python312\python.exe"
if not defined PY call :try_path "%ProgramFiles%\Python312\python.exe"
if defined PY goto venv

echo       Python не найден. Ставлю автоматически, это займёт пару минут...
where winget >nul 2>nul
if errorlevel 1 goto download
winget install -e --id Python.Python.3.12 --silent --accept-package-agreements --accept-source-agreements
call :try_path "%LocalAppData%\Programs\Python\Python312\python.exe"
if not defined PY call :try_path "%ProgramFiles%\Python312\python.exe"
if not defined PY call :try_cmd py -3
if defined PY goto venv

:download
echo       Скачиваю установщик Python с python.org...
curl -L --fail -o "%PY_SETUP%" "%PY_URL%"
if errorlevel 1 powershell -NoProfile -ExecutionPolicy Bypass -Command "[Net.ServicePointManager]::SecurityProtocol='Tls12'; Invoke-WebRequest -Uri '%PY_URL%' -OutFile '%PY_SETUP%'"
if not exist "%PY_SETUP%" goto no_python
echo       Устанавливаю Python...
"%PY_SETUP%" /quiet InstallAllUsers=0 PrependPath=1 Include_launcher=1
call :try_path "%LocalAppData%\Programs\Python\Python312\python.exe"
if not defined PY goto no_python

rem ---------- 2. Виртуальное окружение ----------
:venv
echo [2/4] Создаю виртуальное окружение...
"%PY%" -m venv .venv
if not exist "%VENV_PY%" goto no_venv

rem ---------- 3. Библиотеки ----------
:deps
fc /b requirements.txt ".venv\requirements.installed" >nul 2>nul
if not errorlevel 1 goto run
echo [3/4] Ставлю библиотеки (нужен интернет)...
"%VENV_PY%" -m pip install --disable-pip-version-check -r requirements.txt
if errorlevel 1 goto no_deps
copy /y requirements.txt ".venv\requirements.installed" >nul

rem ---------- 4. Запуск ----------
:run
echo [4/4] Запускаю программу...
start "" /D "%~dp0" "%VENV_PYW%" main.py
exit /b 0

:no_python
echo.
echo ОШИБКА: не получилось установить Python. Проверь интернет и запусти start.bat ещё раз.
pause
exit /b 1

:no_venv
echo.
echo ОШИБКА: не получилось создать виртуальное окружение.
pause
exit /b 1

:no_deps
echo.
echo ОШИБКА: не получилось поставить библиотеки. Проверь интернет и запусти start.bat ещё раз.
pause
exit /b 1

rem ---------- Проверка: подходит ли найденный Python (нужен 3.10+) ----------
:try_cmd
for /f "usebackq delims=" %%i in (`%* -c "import sys;print(sys.executable) if sys.version_info[:2]>=(3,10) else None" 2^>nul`) do set "PY=%%i"
exit /b 0

:try_path
if not exist "%~1" exit /b 0
for /f "usebackq delims=" %%i in (`""%~1" -c "import sys;print(sys.executable) if sys.version_info[:2]>=(3,10) else None"" 2^>nul`) do set "PY=%%i"
exit /b 0
