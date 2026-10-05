---
name: trade-stop
description: Stop the detached trading loop started by /dry-run. Does not close open positions; says so and lists any that are open.
---

1. Run `powershell -ExecutionPolicy Bypass -File scripts/trade_status.ps1` first and note any journal rows with status `open` or `pending_entry`.
2. Run `powershell -ExecutionPolicy Bypass -File scripts/trade_stop.ps1`.
3. Report what was stopped. If positions were open, say clearly that stopping the loop does not close them: in dry-run mode they are simulated and can be ignored; in paper or live mode the user should either restart the loop (it re-adopts and manages them to exit) or close them in the Alpaca dashboard.
