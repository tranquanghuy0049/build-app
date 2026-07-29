@echo off
cd /d "%~dp0"

echo Killing old process on port 8000...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8000" ^| findstr "LISTENING"') do (
    taskkill /f /pid %%a >nul 2>&1
)
timeout /t 2 /nobreak >nul

echo Starting Meeting Summarizer Web...
.\venv\Scripts\python web.py

pause