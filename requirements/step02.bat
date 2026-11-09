@echo off

echo Welcome
echo.

where python >nul 2>&1
if errorlevel 1 (
    echo Python is not installed.
    echo please open the step01.bat file first then try again.
    pause
    exit /b
) else (
    echo python is already installed.
    python --version
)

echo.

where ffmpeg >nul 2>&1
if errorlevel 1 (
    echo ffmpeg is not installed.
    echo please open the step01.bat file first then try again.
    pause
    exit /b
) else (
    echo ffmpeg is already installed.
)

echo.
cd /d "%~dp0"
cd ..

if exist .SMdownloadervenv\Scripts\activate.bat (
    echo venv exists.
) else (
    echo Creating the venv...

    python -m venv .SMdownloadervenv

    if errorlevel 1 (
        echo Failed to create the virtual environment.
        pause
        exit /b
    )

    echo ----Done----
)

echo.
call .SMdownloadervenv\Scripts\activate.bat

echo Installing packages...

python -m pip install --upgrade pip
python -m pip install yt-dlp flask

echo ----Completed----
echo Press enter to exit
pause
exit /b
