
@echo off

where winget >nul 2>&1
if not errorlevel 1 (
    echo Using Winget...

    where python >nul 2>&1
    if errorlevel 1 winget install Python.Python.3.13 -y

    where ffmpeg >nul 2>&1
    if errorlevel 1 winget install Gyan.FFmpeg -y

    where deno >nul 2>&1
    if errorlevel 1 winget install DenoLand.Deno -y

) else (
    where choco >nul 2>&1

    if errorlevel 1 (
        echo Installing Chocolatey...

        powershell -NoProfile -ExecutionPolicy Bypass -Command "Set-ExecutionPolicy Bypass -Scope Process -Force; [System.Net.ServicePointManager]::SecurityProtocol = [System.Net.ServicePointManager]::SecurityProtocol -bor 3072; iex ((New-Object System.Net.WebClient).DownloadString('https://community.chocolatey.org/install.ps1'))"
    )

    set "CHOCO=%ProgramData%\chocolatey\bin\choco.exe"

    if not exist "%CHOCO%" (
        echo Chocolatey installation failed!
        exit /b 1
    )

    where python >nul 2>&1
    if errorlevel 1 "%CHOCO%" install python -y

    where ffmpeg >nul 2>&1
    if errorlevel 1 "%CHOCO%" install ffmpeg -y

    where deno >nul 2>&1
    if errorlevel 1 "%CHOCO%" install deno -y
)

echo.
echo Done!
pause

