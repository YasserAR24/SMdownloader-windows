@echo off

echo Welcome to SMdownloader.
echo.

where python >nul 2>&1
if errorlevel 1 (
    echo Python is not installed.
    echo Please run the step01.bat file first.
    echo Then run this file again.
    echo.
    pause
    exit /b
) else (
    echo Python is already installed.
    python --version
)

echo.

where ffmpeg >nul 2>&1
if errorlevel 1 (
    echo FFmpeg is not installed.
    echo Please run the step01.bat file first.
    echo Then run this file again.
    echo.
    pause
    exit /b
) else (
    echo FFmpeg is already installed.
)

echo.
cd /d "%~dp0"
cd ..

if exist .SMdownloadervenv\Scripts\activate.bat (
    echo The virtual environment already exists.
) else (
    echo Creating the virtual environment...

    python -m venv .SMdownloadervenv

    if errorlevel 1 (
        echo Could not create the virtual environment.
        echo Please try again.
        echo.
        pause
        exit /b
    )

    echo The virtual environment is ready.
)

echo.
call .SMdownloadervenv\Scripts\activate.bat

echo Installing the packages...
echo.

python -m pip install --upgrade pip
python -m pip install yt-dlp flask

echo.
echo ----Done----
pause
exit /b
