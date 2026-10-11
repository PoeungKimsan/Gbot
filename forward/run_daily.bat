@echo off
rem ==============================================================================
rem Forward Paper-Trading Daily Automation Script (Windows Task Scheduler)
rem Protocol: forward_trend_v1 | Run time: 22:30 UTC (17:30 New York / 05:30 ICT)
rem ==============================================================================

cd /d "%~dp0\.."

echo [%date% %time%] Starting forward paper-runner daily execution... >> forward\data\daily_run.log

uv run python -m forward.run_daily >> forward\data\daily_run.log 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [%date% %time%] ERROR: forward.run_daily exited with code %ERRORLEVEL% >> forward\data\daily_run.log
    exit /b %ERRORLEVEL%
)

uv run python -m forward.report >> forward\data\daily_run.log 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [%date% %time%] ERROR: forward.report exited with code %ERRORLEVEL% >> forward\data\daily_run.log
    exit /b %ERRORLEVEL%
)

echo [%date% %time%] Forward daily run completed successfully. >> forward\data\daily_run.log
exit /b 0
