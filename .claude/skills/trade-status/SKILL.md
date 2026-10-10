---
name: trade-status
description: Show whether the trading loop is running, the tail of today's log, and today's journal rows (signals taken, skipped and why, open positions, P&L).
---

Run `powershell -ExecutionPolicy Bypass -File scripts/trade_status.ps1` from the repo root and summarize the output for the user:
- running or not (and the PID)
- the day's bias and VIX regime from the context lines
- every ENTRY / FILLED / EXIT / MISSED / HALT line, with reasons for skips
- open positions with entry, target and stop
- realized P&L for the day

If the log shows `loop error` lines or a HALT, call them out first. If the first line says `RUNNING without a PID file` or `RUNNING with a stale PID file`, call that out too: the loop is orphaned, and it should be stopped with `/trade-stop` before the next scheduled start. Do not restart or stop the loop from this skill; point the user to `/dry-run` or `/trade-stop`.
