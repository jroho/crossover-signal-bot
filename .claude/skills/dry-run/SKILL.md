---
name: dry-run
description: Start the morning 0DTE trading loop in dry-run mode (live quotes, no orders) detached from this session, confirm the day's bias, and report. Bias comes from the data (prior close vs 20-day SMA, 5-day return, VIX regime); an optional bull, bear or neutral argument forces an override and is for testing only.
---

Start today's dry run of the signal bot. The loop uses the data bias; the optional argument (`bull`, `bear` or `neutral`) forces an override for testing only and is passed as `-Bias`. Do not suggest an override based on news or intuition.

Steps:
1. Refresh VIX history: `.venv\Scripts\python.exe -m src.main --config config.toml fetch-vix` (PowerShell, from the repo root).
2. Start the loop detached: `powershell -ExecutionPolicy Bypass -File scripts/trade_start.ps1 -Mode dry-run` plus `-Bias <arg>` when an argument was given. If it reports the loop is already running, say so and stop; do not start a second copy.
3. Wait about 40 seconds (use the Monitor tool with an until-loop on the log file, not a bare sleep), then show the first lines of today's log (`logs/trade_<yyyy-MM-dd>.log`): the mode line and each symbol's context line (`QQQ context: bias=... vix=...`).
4. Report to the user: mode, PID, the data bias per symbol, the VIX regime, and whether a bias override is active.
5. Remind them that `/trade-status` shows the log tail and today's journal, and that the loop stops itself after the 15:35 flat time only for entries; they stop the process with `/trade-stop` at the end of the day.

Never pass `--live`. Never edit `config.toml`. If `scripts/trade_start.ps1` fails, show the error and stop.
