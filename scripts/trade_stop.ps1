# Stops the trading loop started by scripts/trade_start.ps1.
# Open positions are NOT closed by this script; restart the loop and it re-adopts them, or close them in the broker.
$root = Split-Path -Parent $PSScriptRoot
$pidFile = Join-Path $root "logs\trade.pid"
if (-not (Test-Path $pidFile)) { Write-Output "No PID file; nothing to stop."; exit 0 }

$processId = Get-Content $pidFile | Select-Object -First 1
$process = Get-Process -Id $processId -ErrorAction SilentlyContinue
if ($process) {
    Stop-Process -Id $processId -Force
    Write-Output "Stopped trade loop (PID $processId)."
} else {
    Write-Output "Trade loop (PID $processId) was not running."
}
Remove-Item $pidFile -Force
