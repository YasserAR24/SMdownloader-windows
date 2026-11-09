@echo off

echo Welcome
echo.

where python >nul 2>&1

if errorlevel 1 (
    echo Python is not installed.
    echo Installing Python...
    winget install --id Python.Python.3.13
    echo.
    echo python installation completed.
    echo.
) else (
    echo upgrading python...
    winget upgrade --id Python.Python.3.13
    echo.
)

where ffmpeg >nul 2>&1

if errorlevel 1 (
    echo ffmpeg is not installed.
    echo Installing ffmpeg...
    winget install Gyan.FFmpeg
    echo.
    echo ffmpeg installation completed.
    echo.
) else (
    echo upgrading ffmpeg...
    winget upgrade Gyan.FFmpeg
)

echo ----Completed----
echo Press enter to exit...
pause
exit /b

