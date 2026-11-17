Write-Host "Welcome to SMdownloader."
Write-Host "This step02.ps1 file is for creating venv, and installing yt-dlp and flask python libraries"
Write-Host ""

# Check Python
if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Write-Host "Python is not installed." -ForegroundColor Red
    Write-Host "Please run the step01.ps1 file first."
    Write-Host "Then run this file again."
    Write-Host ""
    Read-Host "Press Enter to exit"
    exit 1
}
else {
    Write-Host "Python is already installed."
    python --version
}

Write-Host ""

# Check FFmpeg
if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
    Write-Host "FFmpeg is not installed." -ForegroundColor Red
    Write-Host "Please run the step01.ps1 file first."
    Write-Host "Then run this file again."
    Write-Host ""
    Read-Host "Press Enter to exit"
    exit 1
}
else {
    Write-Host "FFmpeg is already installed."
}

Write-Host ""

# Move to the parent directory of the script's directory
Set-Location (Split-Path -Parent $PSScriptRoot)
$venvPath = Join-Path (Get-Location) ".SMdownloadervenv"
$activateScript = Join-Path $venvPath "Scripts\Activate.ps1"

# Create virtual environment if needed
if (Test-Path $activateScript) {
    Write-Host "The virtual environment already exists."
}
else {
    Write-Host "Creating the virtual environment..."

    python -m venv $venvPath

    if ($LASTEXITCODE -ne 0) {
        Write-Host "Could not create the virtual environment." -ForegroundColor Red
        Write-Host "Please try again."
        Write-Host ""
        Read-Host "Press Enter to exit"
        exit 1
    }

    Write-Host "The virtual environment is ready."
}

Write-Host ""

# Activate virtual environment
. $activateScript

Write-Host "Installing the packages..."
Write-Host ""

python -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) {
    Write-Host "Could not upgrade pip." -ForegroundColor Red
    Read-Host "Press Enter to exit"
    exit 1
}

python -m pip install yt-dlp flask
if ($LASTEXITCODE -ne 0) {
    Write-Host "Package installation failed." -ForegroundColor Red
    Read-Host "Press Enter to exit"
    exit 1
}

Write-Host ""
Write-Host "----Done----"
Write-Host "The requirements have been installed successfully, Now try open the app."
Read-Host "Press Enter to exit..."
