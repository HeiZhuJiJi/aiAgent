@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"

where docker >nul 2>&1
if errorlevel 1 (
    echo ERROR: Docker CLI was not found.
    exit /b 1
)

docker compose version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Docker Compose v2 is not available.
    exit /b 1
)

docker info >nul 2>&1
if errorlevel 1 (
    echo ERROR: Docker Engine is not reachable. Start Docker Desktop before stopping the stack.
    exit /b 1
)

if not exist ".env" (
    echo ERROR: .env is missing, so the existing Compose configuration cannot be resolved safely.
    exit /b 1
)

echo Gracefully stopping Enterprise Agent Work Platform services...
docker compose --env-file ".env" stop --timeout 60
set "STOP_EXIT=%ERRORLEVEL%"

echo.
echo Service status:
docker compose --env-file ".env" ps --all

if not "%STOP_EXIT%"=="0" (
    echo.
    echo ERROR: One or more services did not stop cleanly. Review the output above.
    exit /b %STOP_EXIT%
)

set "FORCED_EXIT=0"
for /f "delims=" %%C in ('docker compose --env-file ".env" ps --all --quiet') do (
    for /f "delims=" %%E in ('docker inspect --format "{{.State.ExitCode}}" %%C') do (
        if "%%E"=="137" set "FORCED_EXIT=1"
    )
)

if "!FORCED_EXIT!"=="1" (
    echo.
    echo ERROR: At least one service exceeded the 60-second grace period and was force-stopped.
    echo Review the service status and logs above; this condition is not hidden as a successful stop.
    exit /b 1
)

echo.
echo Services stopped. PostgreSQL, Procurement DB, MinIO and other named volumes were preserved.
echo Restart with: start.cmd
exit /b 0
