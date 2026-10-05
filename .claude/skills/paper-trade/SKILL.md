---
name: paper-trade
description: Start the morning 0DTE trading loop in PAPER mode (real orders to the Alpaca paper account) detached from this session, confirm the account preflight and the day's bias, and report. Optional argument - bull, bear or neutral - overrides the data bias from the headlines.
---

Start today's paper-trading session of the signal bot. The optional argument is a bias override (`bull`, `bear` or `neutral`); if given, pass it as `-Bias`.

Steps:
1. Refresh VIX history: `.venv\Scripts\python.exe -m src.main --config config.toml fetch-vix` (PowerShell, from the repo root).
2. Start the loop detached: `powershell -ExecutionPolicy Bypass -File scripts/trade_start.ps1 -Mode paper` plus `-Bias <arg>` when an argument was given. If it reports the loop is already running, say so and stop; do not start a second copy.
3. Wait about 40 seconds (a background `until` loop on the log file, not a bare sleep), then show the first lines of today's log (`logs/trade_<yyyy-MM-dd>.log`): the `Account:` preflight line, the mode line, and each symbol's context line. If the log or `.err` file shows a SystemExit from the preflight (key/mode mismatch, account not active, options level), report it verbatim and stop.
4. Report: mode (paper), PID, account status/equity/buying power/options level from the preflight, the data bias per symbol, the VIX regime, and whether a bias override is active. If the user's headline read disagrees with the data bias, tell them to run `/trade-stop` and `/paper-trade <bias>`.
5. Remind them that `/trade-status` shows the log tail and today's journal, that paper orders and fills are also visible in the Alpaca dashboard's Paper view (Orders, Positions, Activities), and that they stop the process with `/trade-stop` after 15:35 ET.

Never pass `--live`. Never edit `config.toml`.
