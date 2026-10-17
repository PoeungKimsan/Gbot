@echo off
title SanAlgo - VAR_117 Multi-Asset ETF Trading Engine
cd /d "%~dp0"

:: Check if port 8080 is already active (another instance is already running)
netstat -ano | findstr ":8080 " | findstr "LISTENING" >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    echo ================================================================
    echo    SanAlgo - VAR_117 Multi-Asset ETF Momentum Rotation Engine   
    echo ================================================================
    echo [!] SanAlgo is ALREADY RUNNING in another window!
    echo [+] Opening web dashboard in your browser: http://127.0.0.1:8080
    echo ================================================================
    start "" http://127.0.0.1:8080
    ping 127.0.0.1 -n 3 >nul
    exit /b 0
)

echo ================================================================
echo    SanAlgo - VAR_117 Multi-Asset ETF Momentum Rotation Engine   
echo ================================================================
echo [+] Starting engine and web dashboard...
echo [+] Dashboard URL: http://127.0.0.1:8080
echo [*] Opening browser in 3 seconds...
echo [*] Press Ctrl+C to stop the engine.
echo ----------------------------------------------------------------
start "" http://127.0.0.1:8080
uv run python -m uvicorn api.main:app --host 127.0.0.1 --port 8080
pause
