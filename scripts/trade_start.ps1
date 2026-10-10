# Starts the trading loop detached from the calling shell, writes a dated log and a PID file.
# Usage: scripts/trade_start.ps1 [-Mode dry-run|paper] [-Bias bull|bear|neutral] [-PollSeconds 20]
param(
    [ValidateSet("dry-run", "paper")] [string]$Mode = "dry-run",
    [ValidateSet("", "bull", "bear", "neutral")] [string]$Bias = "",
    [int]$PollSeconds = 20
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) { throw "Virtual environment not found at $python" }

$pidFile = Join-Path $root "logs\trade.pid"
if (Test-Path $pidFile) {
    $existing = Get-Content $pidFile | Select-Object -First 1
    if ($existing -and (Get-Process -Id $existing -ErrorAction SilentlyContinue)) {
        Write-Output "Trade loop already running (PID $existing). Run scripts/trade_stop.ps1 first."
        exit 1
    }
}
. (Join-Path $PSScriptRoot "trade_procs.ps1")
$running = @(Get-TradeLoopProcess -Root $root)
if ($running.Count -gt 0) {
    Write-Output "Trade loop already running without a PID file (PIDs $(($running | ForEach-Object { $_.ProcessId }) -join ', ')). Run scripts/trade_stop.ps1 first."
    exit 1
}

New-Item -ItemType Directory -Force (Join-Path $root "logs") | Out-Null
$stamp = Get-Date -Format "yyyy-MM-dd"
$log = Join-Path $root "logs\trade_$stamp.log"

$args = @("-u", "-m", "src.main", "--config", "config.toml", "trade", "--poll-seconds", "$PollSeconds")
if ($Mode -eq "dry-run") { $args += "--dry-run" }
if ($Bias) { $args += @("--bias", $Bias) }

$process = Start-Process -FilePath $python -ArgumentList $args -WorkingDirectory $root `
    -RedirectStandardOutput $log -RedirectStandardError "$log.err" -WindowStyle Hidden -PassThru
Set-Content -Path $pidFile -Value $process.Id

Write-Output "Started $Mode trade loop (PID $($process.Id)); bias override: $(if ($Bias) { $Bias } else { 'data' })"
Write-Output "Log: $log"
