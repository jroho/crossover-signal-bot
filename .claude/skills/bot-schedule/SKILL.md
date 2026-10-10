---
name: bot-schedule
description: Show, change or reinstall the Windows scheduled tasks that start the trading loop every market morning and stop it after the close. Settings live in the [schedule] section of config.toml at the project root. Optional argument - "remove" deletes the tasks, "show" only lists them.
---

The bot runs unattended through two Windows Task Scheduler tasks, "SignalBot Morning Start" and "SignalBot Evening Stop", installed by `scripts/schedule_install.ps1` from the `[schedule]` section of `config.toml`:

```
[schedule]
enabled = true
mode = "paper"              # paper or dry-run
bias = ""                   # testing override only; empty = data bias
start_time = "09:20"        # ET
stop_time = "15:40"         # ET
days = "MON,TUE,WED,THU,FRI"
```

Steps:
1. With argument `show` (or no change requested): run `powershell -NoProfile -Command "Get-ScheduledTask -TaskName 'SignalBot*' | Get-ScheduledTaskInfo | Select-Object TaskName, NextRunTime, LastRunTime, LastTaskResult | Format-Table -AutoSize"` and show today's `logs/schedule_<yyyy-MM-dd>.log` if it exists. LastTaskResult 0 means the last run succeeded.
2. With argument `remove`: run `powershell -ExecutionPolicy Bypass -File scripts/schedule_install.ps1 -Remove`.
3. When the user wants a setting changed (time, mode, days, enabled, bias): edit only the `[schedule]` keys in `config.toml` as asked, then run `powershell -ExecutionPolicy Bypass -File scripts/schedule_install.ps1`, which re-registers both tasks and prints their next run times. Editing `[schedule]` is the one config edit this skill may make; never touch `[trading]` or `[alpaca]` here.
4. Report the installed times in both ET and local time, and remind the user that the tasks run only while the PC is awake and signed in (a locked screen is fine; Claude Code does not need to be open). The morning task skips weekends and market holidays by itself.
5. The installer registers both tasks with highest privileges and must run elevated. If it throws "Run this from an administrator PowerShell", tell the user to run the same command from an admin PowerShell.

What the tasks do: the morning task checks the Alpaca calendar, refreshes VIX history, starts the loop with `scripts/trade_start.ps1` in the configured mode, and logs the preflight lines; the evening task stops the loop and runs `parity-check`. Both append to `logs/schedule_<date>.log`. `/trade-status` and `/trade-stop` still work on the scheduled loop.
