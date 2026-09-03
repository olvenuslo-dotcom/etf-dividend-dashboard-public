@echo off
REM Daily domestic ETF refresh - called by Windows Task Scheduler.
REM ASCII only on purpose: cmd.exe reads .bat in the system codepage (cp949),
REM so Korean text here would be mangled and break parsing.
REM Korean logging is done by Python (UTF-8) instead.
REM Register with: tools\register_etf_task.bat

setlocal

set "REPO=%~dp0.."
cd /d "%REPO%"

set "LOG=data\etf_refresh.log"

if not exist "venv\Scripts\python.exe" goto :novenv

REM 1) prices/returns via KRX Open API  2) rebuild the xlsx  3) rebuild the calendar
REM Each step stops the chain on failure so a stale xlsx is never published.
venv\Scripts\python.exe src\etf_refresh.py >> "%LOG%" 2>&1
if errorlevel 1 goto :failed

venv\Scripts\python.exe src\classify.py >> "%LOG%" 2>&1
if errorlevel 1 goto :failed

venv\Scripts\python.exe src\calendar_ics.py >> "%LOG%" 2>&1
if errorlevel 1 goto :failed

exit /b 0

:failed
echo [refresh_etf] step failed - see messages above >> "%LOG%"
exit /b 1

:novenv
echo [refresh_etf] venv not found - run the setup steps in README first >> "data\etf_refresh.log"
exit /b 1
