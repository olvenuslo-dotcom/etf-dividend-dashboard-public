@echo off
REM Register the daily domestic ETF refresh with Windows Task Scheduler.
REM ASCII only - cmd.exe reads .bat in the system codepage.
REM
REM Runs weekdays at 18:30 - after the KRX close (15:30) and after the
REM Open API publishes that day's figures. The market is shut on weekends,
REM so a weekend run would only restamp Friday's numbers.
REM
REM It needs KRX_OPENAPI_KEY in the repo root .env file.
REM
REM Run this once:   tools\register_etf_task.bat
REM Remove it with:  schtasks /Delete /TN "ETF-refresh-daily" /F
REM Check it with:   schtasks /Query /TN "ETF-refresh-daily"
REM Run it now:      schtasks /Run /TN "ETF-refresh-daily"

setlocal
set "REPO=%~dp0.."
pushd "%REPO%"
set "REPO=%CD%"
popd

if not exist "%REPO%\.env" (
    echo WARNING: .env not found - etf_refresh.py needs KRX_OPENAPI_KEY.
    echo Create it first, then run this again.
    echo.
)

schtasks /Create ^
  /TN "ETF-refresh-daily" ^
  /TR "\"%REPO%\tools\refresh_etf.bat\"" ^
  /SC WEEKLY /D MON,TUE,WED,THU,FRI ^
  /ST 18:30 ^
  /F

if errorlevel 1 (
    echo.
    echo Failed to register. If it says access denied, run this from an
    echo Administrator command prompt.
    exit /b %errorlevel%
)

REM schtasks defaults refuse to start on battery power, and never make up a
REM missed run. On a laptop that means the task just sits in "Queued" forever.
REM These three settings need PowerShell - schtasks has no switch for them.
powershell -NoProfile -Command ^
  "$s = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 2) -MultipleInstances IgnoreNew; Set-ScheduledTask -TaskName 'ETF-refresh-daily' -Settings $s | Out-Null"

if errorlevel 1 echo WARNING: could not relax the battery/missed-run settings.

echo.
echo Registered: ETF-refresh-daily  ^(weekdays 18:30, runs on battery too^)
echo Steps:      etf_refresh.py -^> classify.py -^> calendar_ics.py
echo Log file:   %REPO%\data\etf_refresh.log
exit /b 0
