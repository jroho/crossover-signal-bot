# Stops the trading loop started by scripts/trade_start.ps1, including a loop whose PID file is missing.
# Open positions are NOT closed by this script; restart the loop and it re-adopts them, or close them in the broker.
# If any loop process survives (for example "Access is denied" because the loop was started from an administrator
# window and this runs without admin rights), it says so, keeps the PID file and exits 1 instead of reporting success.
$root = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot "trade_procs.ps1")
$pidFile = Join-Path $root "logs\trade.pid"

function Get-LoopIds {
    $ids = @(Get-TradeLoopProcess -Root $root | ForEach-Object { [int]$_.ProcessId })
    if (Test-Path $pidFile) {
        $filePid = Get-Content $pidFile | Select-Object -First 1
        # The PID file can point at a process whose command line is hidden from a non-admin caller; trust it only
        # while that PID is still a python process, never a recycled PID belonging to something else.
        if ($filePid -and (Get-Process -Id $filePid -ErrorAction SilentlyContinue | Where-Object { $_.ProcessName -like "python*" })) {
            $ids += [int]$filePid
        }
    }
    return @($ids | Sort-Object -Unique)
}

$ids = Get-LoopIds
if ($ids.Count -eq 0) {
    if (Test-Path $pidFile) {
        Remove-Item $pidFile -Force
        Write-Output "Trade loop was not running; removed the stale PID file."
    } else {
        Write-Output "No trade loop running; nothing to stop."
    }
    exit 0
}

$errors = @()
foreach ($id in $ids) {
    try {
        Stop-Process -Id $id -Force -ErrorAction Stop
    } catch {
        # A launcher's child can exit on its own once the launcher is stopped; only a survivor is a failure.
        if (Get-Process -Id $id -ErrorAction SilentlyContinue) { $errors += "PID ${id}: $($_.Exception.Message)" }
    }
}
Start-Sleep -Seconds 2
$left = Get-LoopIds

if ($left.Count -eq 0) {
    if (Test-Path $pidFile) { Remove-Item $pidFile -Force }
    Write-Output "Stopped trade loop (PIDs $($ids -join ', '))."
    exit 0
}

Write-Output "FAILED to stop the trade loop; still running: PIDs $($left -join ', ')."
$errors | ForEach-Object { Write-Output "  $_" }
Write-Output "Run scripts/trade_stop.ps1 from an administrator PowerShell, or end those PIDs in Task Manager."
# Keep (or recreate) the PID file so trade_start.ps1 and trade_status.ps1 still see the running loop.
Set-Content -Path $pidFile -Value $left[0]
exit 1
