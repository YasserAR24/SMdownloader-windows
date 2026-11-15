@echo off
cd /d "%~dp0"
echo Welcome to SMdownloader.
echo.
call .SMdownloadervenv\scripts\activate.bat
cd app-files
start "" "http://localhost:5000"
python SMdownloader.py
