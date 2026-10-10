---
name: trade-stop
description: Stop the detached trading loop started by /dry-run. Does not close open positions; says so and lists any that are open.
---

1. Run `powershell -ExecutionPolicy Bypass -File scripts/trade_status.ps1` first and note any journal rows with status `open` or `pending_entry`.
2. Run `powershell -ExecutionPolicy Bypass -File scripts/trade_stop.ps1`.
3. If the stop script prints `FAILED to stop` (exit code 1), report the surviving PIDs and tell the user to run `scripts/trade_stop.ps1` from an administrator PowerShell; skip step 4 until the loop is stopped. Otherwise report what was stopped. If positions were open, say clearly that stopping the loop does not close them: in dry-run mode they are simulated and can be ignored; in paper or live mode the user should either restart the loop (it re-adopts and manages them to exit) or close them in the Alpaca dashboard.
4. Once the loop is stopped, run the `parity-check` skill for today and report its summary line and verdict, so every paper session adds a row to the data-parity log.
