@echo off
rem ==============================================================================
rem Forward Daily Log Automation Script (Windows Task Scheduler)
rem Pre-registration: trend_v1.2 (commit f1affec) | Run time: 00:05 UTC (07:05 ICT)
rem ==============================================================================

cd /d "%~dp0\.."

uv run python forward/forward_log.py
