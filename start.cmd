@echo off
setlocal EnableExtensions
cd /d "%~dp0"

echo [1/5] Checking Docker CLI...
where docker >nul 2>&1
if errorlevel 1 (
    echo ERROR: Docker CLI was not found. Install and start Docker Desktop.
    exit /b 1
)

docker compose version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Docker Compose v2 is not available.
    exit /b 1
)

echo [2/5] Checking Docker Engine...
docker info >nul 2>&1
if errorlevel 1 (
    echo ERROR: Docker Engine is not reachable. Start Docker Desktop and retry.
    exit /b 1
)

if not exist ".env" (
    echo [3/5] Creating local .env with the existing configuration helper...
    call :configure_env
    if errorlevel 1 exit /b 1
) else (
    echo [3/5] Using existing local .env.
)

echo [4/5] Validating Compose configuration...
docker compose --env-file ".env" config --quiet
if errorlevel 1 (
    echo ERROR: Compose configuration is invalid. Review the error above and .env.
    exit /b 1
)

echo [5/5] Building and starting the complete development environment...
if not defined STARTUP_WAIT_TIMEOUT_SECONDS set "STARTUP_WAIT_TIMEOUT_SECONDS=1800"
docker compose --env-file ".env" up --detach --build --wait --wait-timeout %STARTUP_WAIT_TIMEOUT_SECONDS%
set "START_EXIT=%ERRORLEVEL%"

echo.
echo Service status:
docker compose --env-file ".env" ps --all

if not "%START_EXIT%"=="0" (
    echo.
    echo ERROR: One or more services failed to start or become healthy.
    echo Inspect details with: docker compose --env-file .env logs --tail 200 SERVICE
    exit /b %START_EXIT%
)

echo.
echo Enterprise Agent Work Platform is ready.
echo Web: http://localhost:55173
echo API: http://localhost:58080/docs
for /f "delims=" %%A in ('docker compose --env-file ".env" port grafana 3000') do echo Grafana: http://%%A
exit /b 0

:configure_env
if exist ".venv312\Scripts\python.exe" (
    ".venv312\Scripts\python.exe" scripts\configure_local.py
    exit /b %ERRORLEVEL%
)

where py >nul 2>&1
if not errorlevel 1 (
    py -3.12 scripts\configure_local.py
    exit /b %ERRORLEVEL%
)

where python >nul 2>&1
if not errorlevel 1 (
    python scripts\configure_local.py
    exit /b %ERRORLEVEL%
)

echo ERROR: .env is missing and no Python interpreter was found.
echo Install Python 3.12 or create .env from .env.example, then retry.
exit /b 1
