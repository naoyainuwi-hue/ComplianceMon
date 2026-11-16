@echo off
title Compliance Auto-Filter Server
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
    echo Python was not found on this PC. Install Python 3.8+ 
    echo and make sure "Add python.exe to PATH" is checked during install.
    pause
    exit /b 1
)

echo Starting Compliance Auto-Filter server...
echo (Keep this window open while the team uses the tool. Close it to stop the server.)
echo.
python server.py --port 8000

pause
