# DBDMap installer for Windows.
#
# Run from the folder it lives in:
#
#     powershell -ExecutionPolicy Bypass -File .\install.ps1
#
# Installs Tesseract (via winget) if missing, creates a virtualenv, and writes a
# DBDMap.bat launcher.

$ErrorActionPreference = 'Stop'
Set-Location -Path $PSScriptRoot

function Say  ($m) { Write-Host "==> $m" -ForegroundColor Cyan }
function Warn ($m) { Write-Host "warn $m" -ForegroundColor Yellow }
function Die  ($m) { Write-Host "!!  $m" -ForegroundColor Red; exit 1 }

# ---------------------------------------------------------------------------
# Python
# ---------------------------------------------------------------------------
$python = $null
foreach ($candidate in @('py -3', 'python', 'python3')) {
    $exe, $arg = $candidate -split ' ', 2
    if (Get-Command $exe -ErrorAction SilentlyContinue) {
        try {
            $version = & $exe $arg --version 2>&1
            if ($version -match 'Python 3\.(\d+)' -and [int]$Matches[1] -ge 9) {
                $python = $candidate
                Say "Using $version"
                break
            }
        } catch { }
    }
}

if (-not $python) {
    Warn 'Python 3.9+ not found. Installing via winget...'
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        Die 'winget is unavailable. Install Python 3 from https://www.python.org/downloads/ and re-run this script.'
    }
    winget install --id Python.Python.3.12 -e --accept-source-agreements --accept-package-agreements
    Die 'Python was installed. Close this window, open a NEW terminal, and run install.ps1 again.'
}

# ---------------------------------------------------------------------------
# Tesseract OCR
# ---------------------------------------------------------------------------
$tesseractPaths = @(
    'C:\Program Files\Tesseract-OCR\tesseract.exe',
    'C:\Program Files (x86)\Tesseract-OCR\tesseract.exe',
    "$env:LOCALAPPDATA\Programs\Tesseract-OCR\tesseract.exe"
)
$haveTesseract = (Get-Command tesseract -ErrorAction SilentlyContinue) -or
                 ($tesseractPaths | Where-Object { Test-Path $_ })

if (-not $haveTesseract) {
    Say 'Installing Tesseract OCR...'
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        winget install --id UB-Mannheim.TesseractOCR -e --accept-source-agreements --accept-package-agreements
    } else {
        Die 'winget is unavailable. Install Tesseract from https://github.com/UB-Mannheim/tesseract/wiki and re-run this script.'
    }
} else {
    Say 'Tesseract already installed.'
}

# ---------------------------------------------------------------------------
# Virtualenv
# ---------------------------------------------------------------------------
Say 'Creating virtualenv in .venv'
$exe, $arg = $python -split ' ', 2
if ($arg) { & $exe $arg -m venv .venv } else { & $exe -m venv .venv }

Say 'Installing Python dependencies (this can take a minute)'
& .\.venv\Scripts\python.exe -m pip install --upgrade pip --quiet
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt --quiet

# ---------------------------------------------------------------------------
# Launcher
# ---------------------------------------------------------------------------
$launcher = @"
@echo off
cd /d "%~dp0"
".venv\Scripts\python.exe" dbdmap.py %*
pause
"@
Set-Content -Path 'DBDMap.bat' -Value $launcher -Encoding ASCII

Say 'Done.'
Write-Host ''
Write-Host '  Start DBDMap with:   DBDMap.bat        (or double-click it)'
Write-Host '  Check your setup:    DBDMap.bat --doctor'
Write-Host ''
Write-Host 'The first run asks a few questions and writes config.ini.'
Write-Host 'Set Dead by Daylight to Borderless Window so the overlay is visible.' -ForegroundColor Yellow
