$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot

if (Get-Command py -ErrorAction SilentlyContinue) {
    py -3 app.py
} else {
    python app.py
}

if ($LASTEXITCODE -ne 0) {
    Write-Host "Instagram Downloader stopped with an error. Check that Python 3.11 or newer is installed."
    Read-Host "Press Enter to close"
}
