# Shared helper for trade_start.ps1, trade_stop.ps1 and trade_status.ps1: finds running trade-loop processes by
# command line, so a loop is found even when logs\trade.pid is missing. On Windows the venv python.exe is a launcher
# that spawns a child interpreter with the same command line, so one loop normally shows up as two processes.
# A process started from an administrator window may hide its command line from a non-admin caller; the scripts
# fall back to the PID file in that case.
function Get-TradeLoopProcess {
    param([Parameter(Mandatory = $true)][string]$Root)
    Get-CimInstance Win32_Process -Filter "Name like 'python%'" -ErrorAction SilentlyContinue | Where-Object {
        $_.CommandLine -and
        $_.CommandLine.IndexOf($Root, [StringComparison]::OrdinalIgnoreCase) -ge 0 -and
        $_.CommandLine -match '-m\s+src\.main\b.*\strade(\s|$)'
    }
}
