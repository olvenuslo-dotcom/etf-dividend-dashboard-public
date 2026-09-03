@echo off
REM DDR5 spot price daily collector - called by Windows Task Scheduler.
REM ASCII only on purpose: cmd.exe reads .bat in the system codepage (cp949),
REM so Korean text here would be mangled and break parsing.
REM Korean logging is done by Python (UTF-8) instead.
REM Register with: tools\register_ddr5_task.bat

setlocal

set "REPO=%~dp0.."
cd /d "%REPO%"

if not exist "venv\Scripts\python.exe" goto :novenv

venv\Scripts\python.exe src\ddr5.py >> "data\ddr5_collect.log" 2>&1
exit /b %errorlevel%

:novenv
echo venv not found - run the setup steps in README first >> "data\ddr5_collect.log"
exit /b 1
