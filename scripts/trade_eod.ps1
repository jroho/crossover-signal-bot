# Scheduled end of day: note any open positions, stop the trade loop, run the data parity check for the day.
# Stopping does not close positions; the loop re-adopts them next start, or close them in the Alpaca dashboard.
$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$python = Join-Path $root ".venv\Scripts\python.exe"
$stamp = Get-Date -Format "yyyy-MM-dd"
New-Item -ItemType Directory -Force (Join-Path $root "logs") | Out-Null
$log = Join-Path $root "logs\schedule_$stamp.log"

function Log($text) {
    $line = "[{0}] {1}" -f (Get-Date -Format "HH:mm:ss"), $text
    Add-Content -Path $log -Value $line -Encoding utf8
    Write-Output $line
}

Log "end of day"
$status = & (Join-Path $root "scripts\trade_status.ps1") 2>&1
$status | Select-Object -First 1 | ForEach-Object { Log $_ }
$open = $status | Select-String -Pattern "\s(open|pending_entry)\s"
if ($open) {
    Log "WARNING: journal shows open rows; stopping the loop does not close them:"
    $open | ForEach-Object { Log "  $($_.Line)" }
}

try {
    & (Join-Path $root "scripts\trade_stop.ps1") 2>&1 | ForEach-Object { Log $_ }
    if ($LASTEXITCODE -ne 0) { Log "STOP FAILED: the trade loop is still running; see the lines above." }
} catch {
    Log "stop failed: $_"
}

& $python -m src.main --config config.toml parity-check 2>&1 | ForEach-Object { Log $_ }
