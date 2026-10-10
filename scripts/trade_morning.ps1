# Scheduled morning start: skip holidays, refresh VIX, start the trade loop in the configured mode, then record the
# preflight lines. Settings come from [schedule] in config.toml (see scripts/schedule_install.ps1).
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

$settings = @{}
& $python (Join-Path $root "scripts\schedule_settings.py") (Join-Path $root "config.toml") | ForEach-Object {
    $key, $value = $_ -split "=", 2
    $settings[$key] = $value
}
Log "morning start: mode=$($settings.mode) bias=$(if ($settings.bias) { $settings.bias } else { 'data' })"

if ($settings.enabled -ne "true") { Log "schedule disabled in config.toml; nothing started."; exit 0 }

& $python (Join-Path $root "scripts\market_day.py") 2>&1 | ForEach-Object { Log $_ }
if ($LASTEXITCODE -ne 0) { exit 0 }

& $python -m src.main --config config.toml fetch-vix 2>&1 | ForEach-Object { Log $_ }

# Call the start script in-process. A nested powershell.exe would hand its stdout pipe to the detached loop and
# this script would then block on that pipe until the loop exits.
$startArgs = @{ Mode = $settings.mode }
if ($settings.bias) { $startArgs.Bias = $settings.bias }
try {
    & (Join-Path $root "scripts\trade_start.ps1") @startArgs 2>&1 | ForEach-Object { Log $_ }
} catch {
    Log "start failed: $_"
}

Start-Sleep -Seconds 45
$tradeLog = Join-Path $root "logs\trade_$stamp.log"
if (Test-Path $tradeLog) {
    Get-Content $tradeLog -TotalCount 6 | ForEach-Object { Log "  $_" }
}
$errFile = "$tradeLog.err"
if ((Test-Path $errFile) -and (Get-Item $errFile).Length -gt 0) {
    Log "PREFLIGHT ERROR:"
    Get-Content $errFile -Tail 5 | ForEach-Object { Log "  $_" }
}
