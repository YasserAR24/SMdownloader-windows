Write-Host "Welcome to SMdownloader."
Write-Host "This step01.ps1 file is for installing python and FFmpeg"
Write-Host ""

# Check Winget
if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
    Write-Host "Winget was not found on your computer." -ForegroundColor Red
    Write-Host 'Please install or update "App Installer" from the Microsoft Store.'
    Write-Host "Then run this file again."
    Read-Host "Press Enter to exit"
    exit 1
}

# Check Python
if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Write-Host "Python is not installed."
    Write-Host "Installing Python..."

    winget install --id Python.Python.3.13 -e --accept-package-agreements --accept-source-agreements

    if ($LASTEXITCODE -ne 0) {
        Write-Host "Python installation failed." -ForegroundColor Red
        Read-Host "Press Enter to exit"
        exit 1
    }

    Write-Host ""
    Write-Host "Python installation is finished."
    Write-Host ""
}
else {
    Write-Host "Python is already installed."
    Write-Host "Checking for a new version..."

    winget upgrade --id Python.Python.3.13 -e --accept-package-agreements --accept-source-agreements
    Write-Host ""
}

# Check FFmpeg
if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
    Write-Host "FFmpeg is not installed."
    Write-Host "Installing FFmpeg..."

    winget install --id Gyan.FFmpeg -e --accept-package-agreements --accept-source-agreements

    if ($LASTEXITCODE -ne 0) {
        Write-Host "FFmpeg installation failed." -ForegroundColor Red
        Read-Host "Press Enter to exit"
        exit 1
    }

    Write-Host ""
    Write-Host "FFmpeg installation is finished."
    Write-Host ""
}
else {
    Write-Host "FFmpeg is already installed."
    Write-Host "Checking for a new version..."

    winget upgrade --id Gyan.FFmpeg -e --accept-package-agreements --accept-source-agreements
}

Write-Host ""
Write-Host "----Done----"
Write-Host "Now open step02.ps1 file"
Read-Host "Press Enter to exit..."
