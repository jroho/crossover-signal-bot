---
name: enable-sip
description: Switch the bot from free IEX/indicative data to real-time SIP stock bars and OPRA option quotes once the Alpaca Algo Trader Plus subscription is active and the OPRA agreement is accepted. Verifies the entitlement with live requests first and changes nothing if either check fails. Run it when the user says the subscription is approved.
---

Move the live loop onto paid consolidated data. The volume filter is worth about ten points of win rate in the backtest, and the free IEX feed measures volume with only ~56% agreement to SIP, so this switch is what makes the live grade match the backtested grade.

Steps:
1. From the repo root run `.venv\Scripts\python.exe scripts\check_data_plan.py`. It requests the last 10 minutes of SIP bars and an OPRA quote for a current SPY contract, the same endpoints the loop uses.
2. If either line says FAIL, report the exact error text and stop. "subscription" in the SIP error means Algo Trader Plus is not active yet; "OPRA agreement is not signed" means the agreement step in the Alpaca dashboard is still pending. Nothing was changed.
3. If both PASS, run `.venv\Scripts\python.exe scripts\check_data_plan.py --apply`. It sets `[alpaca] live_feed = "sip"` and `[trading] option_feed = "opra"` in config.toml and prints the reloaded values. This is the one skill allowed to edit config.toml, because the user asked for exactly this change.
4. Check whether the trade loop is running (`powershell -ExecutionPolicy Bypass -File scripts/trade_status.ps1`). A running loop keeps the old feeds until restarted: if it is running with no open positions, say that `/trade-stop` then `/paper-trade` will pick up the new feeds; if positions are open, wait for the day's `/trade-stop`. If it is not running, the next `/paper-trade` uses the new feeds automatically.
5. Tell the user the next day's `/parity-check` (run by `/trade-stop`) should show grade and volume-grade mismatches near zero, which is the confirmation the subscription did its job. If it does not, something else differs and should be investigated before trusting the feed.
