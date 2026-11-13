@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
title Chistilshchik

rem PY хранит готовую команду запуска Python, например: py -3
rem или "C:\Users\Андрей\...\python.exe" (в кавычках, поэтому без кавычек при вызове).
rem Путь не считываем из вывода Python: при русских буквах в пути
rem (папка пользователя «Андрей») он портится.
set "PY="
set "VENV_PY=.venv\Scripts\python.exe"
set "VENV_PYW=.venv\Scripts\pythonw.exe"
set "PY_URL=https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe"
set "PY_SETUP=%TEMP%\python-3.12.10-amd64.exe"

if exist "%VENV_PY%" goto deps

rem ---------- 1. Ищем Python ----------
echo [1/4] Ищу Python...
call :find_python
if defined PY goto venv

echo       Python не найден. Ставлю автоматически, это займёт пару минут...
where winget >nul 2>nul
if errorlevel 1 goto download
winget install -e --id Python.Python.3.12 --silent --accept-package-agreements --accept-source-agreements
call :find_python
if defined PY goto venv

:download
echo       Скачиваю установщик Python с python.org...
curl -L --fail -o "%PY_SETUP%" "%PY_URL%"
if errorlevel 1 powershell -NoProfile -ExecutionPolicy Bypass -Command "[Net.ServicePointManager]::SecurityProtocol='Tls12'; Invoke-WebRequest -Uri '%PY_URL%' -OutFile '%PY_SETUP%'"
if not exist "%PY_SETUP%" goto no_python
echo       Устанавливаю Python...
"%PY_SETUP%" /quiet InstallAllUsers=0 PrependPath=1 Include_launcher=1
call :find_python
if not defined PY goto no_python

rem ---------- 2. Виртуальное окружение ----------
:venv
echo [2/4] Создаю виртуальное окружение...
%PY% -m venv .venv
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
echo Попробуй удалить папку .venv рядом с start.bat и запустить ещё раз.
pause
exit /b 1

:no_deps
echo.
echo ОШИБКА: не получилось поставить библиотеки. Проверь интернет и запусти start.bat ещё раз.
pause
exit /b 1

rem ---------- Поиск подходящего Python (3.10 и новее) ----------
:find_python
call :check py -3
if defined PY exit /b 0
for %%v in (314 313 312 311 310) do if not defined PY call :check "%LocalAppData%\Programs\Python\Python%%v\python.exe"
if defined PY exit /b 0
for %%v in (314 313 312 311 310) do if not defined PY call :check "%ProgramFiles%\Python%%v\python.exe"
if defined PY exit /b 0
call :check python
exit /b 0

rem Пробуем запустить; подходит, если Python ответил «версия годится» (код 0).
rem Заглушка Microsoft Store вернёт ошибку, поэтому её не примем.
:check
%* -c "import sys; sys.exit(0 if sys.version_info[:2] >= (3, 10) else 1)" >nul 2>nul
if not errorlevel 1 set "PY=%*"
exit /b 0
