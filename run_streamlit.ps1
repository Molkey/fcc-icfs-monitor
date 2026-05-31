$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot

if (-not (Test-Path -LiteralPath ".\.venv\Scripts\python.exe")) {
    python -m venv .venv
}

try {
    .\.venv\Scripts\python.exe --version | Out-Null
} catch {
    Write-Host "The local .venv Python is not runnable." -ForegroundColor Yellow
    Write-Host "Install Python 3.10-3.14 from https://www.python.org/downloads/, then delete C:\workspace\.venv and run this script again." -ForegroundColor Yellow
    throw
}

.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m streamlit run .\streamlit_app.py
