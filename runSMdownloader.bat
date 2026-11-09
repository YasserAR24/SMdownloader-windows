@echo off
cd /d "%~dp0"
call .SMdownloadervenv\scripts\activate.bat
cd app-code
start "" "http://localhost:5000"
python web-downloader.py
