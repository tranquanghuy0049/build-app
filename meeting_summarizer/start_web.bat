@echo off
cd /d "%~dp0"

REM Find an interpreter that can actually run the app. The old hard-coded
REM .\venv\ does not exist in this checkout, so the script failed without
REM printing anything useful. .venv-build is created by
REM packaging\build_windows.ps1 and holds exactly the runtime dependency set,
REM which makes it the best fallback.
set PY=
if exist "venv\Scripts\python.exe" set PY=venv\Scripts\python.exe
if not defined PY if exist ".venv-build\Scripts\python.exe" set PY=.venv-build\Scripts\python.exe
if not defined PY if exist "..\.venv\Scripts\python.exe" set PY=..\.venv\Scripts\python.exe

if not defined PY (
  echo.
  echo Khong tim thay moi truong Python nao de chay.
  echo Tao mot cai bang cac lenh sau roi chay lai file nay:
  echo.
  echo   py -3 -m venv venv
  echo   venv\Scripts\python -m pip install torch torchaudio --index-url https://download.pytorch.org/whl/cpu
  echo   venv\Scripts\python -m pip install --no-deps chunkformer==1.2.2
  echo   venv\Scripts\python -m pip install -r requirements-win.txt
  echo.
  pause
  exit /b 1
)

echo Dung interpreter: %PY%

REM Fail early and clearly if the environment is missing a runtime dependency,
REM rather than after the browser has already opened on a broken page.
"%PY%" -c "import fastapi, torch, chunkformer; from google import genai" 2>nul
if errorlevel 1 (
  echo.
  echo Moi truong %PY% thieu thu vien. Cai bo sung:
  echo.
  echo   "%PY%" -m pip install torch torchaudio --index-url https://download.pytorch.org/whl/cpu
  echo   "%PY%" -m pip install --no-deps chunkformer==1.2.2
  echo   "%PY%" -m pip install -r requirements-win.txt
  echo.
  pause
  exit /b 1
)

echo Killing old process on port 8000...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8000" ^| findstr "LISTENING"') do (
    taskkill /f /pid %%a >nul 2>&1
)
timeout /t 2 /nobreak >nul

echo Starting Meeting Summarizer Web tai http://127.0.0.1:8000 ...
"%PY%" web.py

pause
