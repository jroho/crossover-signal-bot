# Prints whether the loop is running, the tail of today's log, and today's journal rows.
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$pidFile = Join-Path $root "logs\trade.pid"
$stamp = Get-Date -Format "yyyy-MM-dd"
$log = Join-Path $root "logs\trade_$stamp.log"

. (Join-Path $PSScriptRoot "trade_procs.ps1")
$loopIds = (@(Get-TradeLoopProcess -Root $root) | ForEach-Object { $_.ProcessId }) -join ", "
if (Test-Path $pidFile) {
    $processId = Get-Content $pidFile | Select-Object -First 1
    if (Get-Process -Id $processId -ErrorAction SilentlyContinue) { Write-Output "RUNNING (PID $processId)" }
    elseif ($loopIds) { Write-Output "RUNNING with a stale PID file (loop PIDs $loopIds); stop it with scripts/trade_stop.ps1" }
    else { Write-Output "NOT RUNNING (stale PID $processId)" }
} elseif ($loopIds) {
    Write-Output "RUNNING without a PID file (loop PIDs $loopIds); stop it with scripts/trade_stop.ps1"
} else {
    Write-Output "NOT RUNNING (no PID file)"
}

if (Test-Path $log) {
    Write-Output "--- $log (last 25 lines) ---"
    Get-Content $log -Tail 25
    if ((Test-Path "$log.err") -and ((Get-Item "$log.err").Length -gt 0)) {
        Write-Output "--- stderr ---"
        Get-Content "$log.err" -Tail 10
    }
} else {
    Write-Output "No log for today at $log"
}

$journal = Join-Path $root "logs\live_trades.csv"
if (Test-Path $journal) {
    Write-Output "--- journal rows dated $stamp ---"
    Import-Csv $journal | Where-Object { $_.opened_at -like "$stamp*" } |
        Select-Object symbol, direction, grade, occ_symbol, contracts, status, entry_price, exit_reason, exit_price, pnl_usd, decision |
        Format-Table -AutoSize | Out-String -Width 200
}
