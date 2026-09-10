@echo off
rem ===========================================================
rem  measauto UI launcher  --  double-click this file.
rem
rem  ASCII ONLY, on purpose.  cmd.exe reads .bat files in the
rem  system code page (949 here), not UTF-8, so Korean text in
rem  this file gets mangled and breaks the parser.  Any message
rem  the user needs to read in Korean is shown by measauto_ui.py
rem  itself (see _fatal), which has no such limit.
rem
rem  run_ui.ps1 does the same thing for terminal use.
rem ===========================================================
setlocal
cd /d "%~dp0"
title measauto UI

set "PY=%~dp0.venv\Scripts\python.exe"
set "PYW=%~dp0.venv\Scripts\pythonw.exe"

if not exist "%PYW%" goto :novenv

rem Check dependencies BEFORE launching.  pythonw has no console,
rem so a failure after launch would show nothing at all.
"%PY%" -c "import PySide6, matplotlib" 2>nul
if errorlevel 1 goto :install

:launch
rem pythonw = no console window.  start = this batch exits right away.
start "" "%PYW%" "%~dp0measauto_ui.py"
exit /b 0

:install
echo.
echo   First run: installing PySide6 + matplotlib.
echo   This can take a few minutes. Do not close this window.
echo.
"%PY%" -m pip install PySide6 matplotlib
if errorlevel 1 goto :pipfailed
echo.
echo   Done. Starting the UI...
goto :launch

:novenv
echo.
echo   [!] Virtual environment not found:
echo       %~dp0.venv
echo.
echo   Run these in this folder first:
echo       python -m venv .venv
echo       .venv\Scripts\python.exe -m pip install -r requirements.txt
echo.
pause
exit /b 1

:pipfailed
echo.
echo   [!] pip install failed. Read the messages above.
echo.
pause
exit /b 1
