---
name: parity-check
description: After the close, re-evaluate a trading day on full SIP bars and compare grades and entry decisions with what the live loop saw on free IEX data; keeps a running mismatch log that decides whether the paid Alpaca data plan is worth it. Optional argument - a date YYYY-MM-DD, default today.
---

Measure the free-data gap for one session. The live loop grades the newest 15 minutes of every poll on IEX bars with scaled volume; the backtest graded consolidated SIP bars. This check re-grades the whole day on SIP bars with the same evaluator, replays the engine's entry decisions on them, and compares.

Steps:
1. From the repo root run `.venv\Scripts\python.exe -m src.main --config config.toml parity-check`, adding `--date <arg>` when a date was given. It needs the loop's evaluations in `logs/signals.sqlite3` and SIP bars at least 16 minutes old, so run it after the loop is stopped for the day; earlier, the newest minutes are reported as "without SIP bars yet" and are simply not compared.
2. Report the summary line it prints, then from the report it names (`logs/parity/parity_<date>.md`): the entries-match verdict, each decision-relevant grade mismatch (time, symbol, direction, live grade vs SIP grade, the two volume grades), and each decision that appears on only one side. Skips for halted / max open positions / no contract are replay artifacts and are already excluded.
3. Read `logs/parity/parity_log.csv` and give the running totals: days checked, days with any decision-relevant mismatch, days where the entries differed. State plainly whether the IEX data gap has changed a trade yet. Do not recommend the subscription from a single day.

Never edit `config.toml`. This check never places orders; the replay journal lives under `logs/parity/replay/`.
