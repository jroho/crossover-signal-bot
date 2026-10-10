# Installs (or refreshes) the two Windows scheduled tasks that run the bot every market day, from the [schedule]
# section of config.toml. Re-run after changing those settings. Use -Remove to delete the tasks.
#
#   powershell -ExecutionPolicy Bypass -File scripts/schedule_install.ps1
#   powershell -ExecutionPolicy Bypass -File scripts/schedule_install.ps1 -Remove
#
# Tasks run as the current user while logged on, so the PC must be awake and signed in during market hours; Claude
# Code does not need to be open. They run with highest privileges so the evening task can stop a loop that was
# started from an administrator window, so this installer must itself run from an administrator PowerShell.
param([switch]$Remove)

$ErrorActionPreference = "Stop"
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
if (-not ([Security.Principal.WindowsPrincipal]$identity).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "Run this from an administrator PowerShell: the tasks are registered with highest privileges."
}
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
$startName = "SignalBot Morning Start"
$stopName = "SignalBot Evening Stop"

if ($Remove) {
    foreach ($name in @($startName, $stopName)) {
        if (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue) {
            Unregister-ScheduledTask -TaskName $name -Confirm:$false
            Write-Output "Removed task '$name'."
        }
    }
    exit 0
}

$settings = @{}
& $python (Join-Path $root "scripts\schedule_settings.py") (Join-Path $root "config.toml") | ForEach-Object {
    $key, $value = $_ -split "=", 2
    $settings[$key] = $value
}
if ($LASTEXITCODE -ne 0) { throw "could not read [schedule] from config.toml" }

if ($settings.enabled -ne "true") {
    Write-Output "[schedule] enabled = false: removing any installed tasks."
    & $PSCommandPath -Remove
    exit 0
}

$dayMap = @{ MON = "Monday"; TUE = "Tuesday"; WED = "Wednesday"; THU = "Thursday"; FRI = "Friday"; SAT = "Saturday"; SUN = "Sunday" }
$days = $settings.days.ToUpper().Split(",") | ForEach-Object { $dayMap[$_.Trim()] }

$taskSettings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 30) -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId $identity.Name -LogonType Interactive -RunLevel Highest

$jobs = @(
    @{ Name = $startName; Script = "scripts\trade_morning.ps1"; At = $settings.start_local; Label = "start $($settings.mode) loop ($($settings.start_time) ET)" },
    @{ Name = $stopName;  Script = "scripts\trade_eod.ps1";     At = $settings.stop_local;  Label = "stop loop + parity check ($($settings.stop_time) ET)" }
)
foreach ($job in $jobs) {
    $action = New-ScheduledTaskAction -Execute "powershell.exe" `
        -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$(Join-Path $root $job.Script)`"" `
        -WorkingDirectory $root
    $trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek $days -At $job.At
    Register-ScheduledTask -TaskName $job.Name -Action $action -Trigger $trigger -Settings $taskSettings -Principal $principal `
        -Description "Intraday Signal Bot: $($job.Label). Settings: [schedule] in config.toml; reinstall with scripts/schedule_install.ps1." -Force | Out-Null
    Write-Output "Installed '$($job.Name)': $($job.Label), local time $($job.At), days $($settings.days)"
}

Get-ScheduledTask -TaskName "SignalBot*" | Get-ScheduledTaskInfo | Select-Object TaskName, NextRunTime, LastRunTime, LastTaskResult | Format-Table -AutoSize
