@echo off
REM Turn on the repo's git hooks on this PC. Run once after cloning:
REM     tools\install_hooks.bat
REM
REM Hooks are NOT copied by git clone, and core.hooksPath is per-clone
REM config - so a new PC has no DRM guard until this runs. That is how the
REM 2026-08-07 incident happened: the PC that committed had no hook.
REM
REM ASCII only - cmd.exe reads .bat in the system codepage.

setlocal
set "REPO=%~dp0.."
pushd "%REPO%"

git config core.hooksPath .githooks
if errorlevel 1 (
    echo Failed - is this a git repository?
    popd
    exit /b 1
)

echo Hooks enabled: .githooks
echo   pre-commit  blocks DRM-wrapped files and .env from being committed
echo   pre-push    blocks them from being pushed, even if the commit came
echo               from a PC without the hook
echo.
echo Check with:  git config core.hooksPath
popd
exit /b 0
