@echo off
echo ==========================================
echo   ReadabilityRSS - Local Startup Script
echo ==========================================

set "ROOT_DIR=%~dp0"
set "LOCAL_BACKEND_URL=http://localhost:8001"

:: Start Backend in a new window
echo [1/3] Starting Backend (FastAPI)...
start "RRSS Backend" cmd /k "cd /d %ROOT_DIR% && set PYTHONPATH=%ROOT_DIR% && backend\venv\Scripts\python.exe -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8001 --reload"

:: Start Dashboard in a new window
echo [2/3] Starting Dashboard (React)...
start "RRSS Dashboard" cmd /k "cd /d %ROOT_DIR%frontend && set PORT=3010 && set BROWSER=none && npm start"

:: Start Reader in a new window
echo [3/3] Starting Reader (Vite)...
start "RRSS Reader" cmd /k "cd /d %ROOT_DIR%reader && npm run dev -- --host 0.0.0.0 --port 5173"

echo.
echo All services are launching in separate windows.
echo Both dev servers proxy /api to %LOCAL_BACKEND_URL%
echo Keep those windows open while you are working!
echo.
pause
