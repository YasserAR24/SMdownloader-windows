$ErrorActionPreference = "Stop"

# Project paths
$ProjectRoot = $PSScriptRoot
$VenvPython = Join-Path $ProjectRoot ".SMdownloadervenv\Scripts\python.exe"
$AppDirectory = Join-Path $ProjectRoot "app-files"
$AppScript = Join-Path $AppDirectory "SMdownloader.py"

# Welcome message
Clear-Host
Write-Host "       Welcome to SMdownloader!" -ForegroundColor Cyan
Write-Host ""

# Check virtual environment
if (!(Test-Path $VenvPython)) {
    Write-Host "ERROR: Virtual environment not found!" -ForegroundColor Red
    Write-Host "Please run the setup script first."
    Read-Host "Press Enter to exit"
    exit 1
}

# Check application
if (!(Test-Path $AppScript)) {
    Write-Host "ERROR: SMdownloader.py not found!" -ForegroundColor Red
    Read-Host "Press Enter to exit"
    exit 1
}

# Open browser
Write-Host "Opening SMdownloader..." -ForegroundColor Green
Start-Process "http://localhost:5000"

# Run application
Write-Host "Starting Flask application..." -ForegroundColor Yellow
Write-Host ""

Push-Location $AppDirectory

try {
    & $VenvPython $AppScript
}
finally {
    Pop-Location
}

# Exit
Write-Host ""
Write-Host "SMdownloader stopped." -ForegroundColor Cyan
Read-Host "Press Enter to exit"

