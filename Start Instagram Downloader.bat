@echo off
setlocal
cd /d "%~dp0"

where py >nul 2>nul
if %errorlevel%==0 (
  py -3 app.py
) else (
  python app.py
)

if errorlevel 1 (
  echo.
  echo Instagram Downloader stopped with an error. Check that Python 3.11 or newer is installed.
  pause
)
