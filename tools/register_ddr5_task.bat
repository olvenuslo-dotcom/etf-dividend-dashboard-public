@echo off
REM Register the daily DDR5 collector with Windows Task Scheduler.
REM ASCII only - cmd.exe reads .bat in the system codepage.
REM
REM Runs weekdays at 18:00. The DRAM spot market does not quote on weekends,
REM so a weekend run would just stamp Friday's numbers with a new date.
REM
REM Run this once:   tools\register_ddr5_task.bat
REM Remove it with:  schtasks /Delete /TN "ETF-DDR5-daily" /F
REM Check it with:   schtasks /Query /TN "ETF-DDR5-daily"
REM Run it now:      schtasks /Run /TN "ETF-DDR5-daily"

setlocal
set "REPO=%~dp0.."
pushd "%REPO%"
set "REPO=%CD%"
popd

schtasks /Create ^
  /TN "ETF-DDR5-daily" ^
  /TR "\"%REPO%\tools\collect_ddr5.bat\"" ^
  /SC WEEKLY /D MON,TUE,WED,THU,FRI ^
  /ST 18:00 ^
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
  "$s = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 1) -MultipleInstances IgnoreNew; Set-ScheduledTask -TaskName 'ETF-DDR5-daily' -Settings $s | Out-Null"

if errorlevel 1 echo WARNING: could not relax the battery/missed-run settings.

echo.
echo Registered: ETF-DDR5-daily  ^(weekdays 18:00, runs on battery too^)
echo Log file:   %REPO%\data\ddr5_collect.log
exit /b 0
