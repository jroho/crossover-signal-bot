"""Check whether the Alpaca account can read real-time SIP stock bars and OPRA option quotes, and optionally
switch config.toml to those feeds.

    python scripts/check_data_plan.py           # report PASS/FAIL per feed, change nothing
    python scripts/check_data_plan.py --apply   # when both pass, set live_feed = "sip" and option_feed = "opra"

Both checks hit the exact endpoints the trade loop uses. On the free Basic plan the SIP check fails with a
subscription error and the OPRA check fails with "OPRA agreement is not signed"; after Algo Trader Plus is active
and the OPRA agreement is accepted, both pass. The exit code is 0 only when both pass.
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.config import load_config  # noqa: E402
from src.execution.broker import AlpacaBroker  # noqa: E402


def check_sip(config) -> tuple[bool, str]:
    from alpaca.data.enums import DataFeed
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame

    client = StockHistoricalDataClient(api_key=config.alpaca.api_key_id, secret_key=config.alpaca.api_secret_key)
    now = datetime.now(tz=UTC)
    # The free plan withholds the newest 15 minutes of SIP data; asking for the last 10 minutes is the real test.
    request = StockBarsRequest(symbol_or_symbols="QQQ", timeframe=TimeFrame.Minute, start=now - timedelta(minutes=10), end=now, feed=DataFeed.SIP)
    try:
        bars = client.get_stock_bars(request).data.get("QQQ", [])
    except Exception as exc:  # noqa: BLE001 - the error text is the diagnosis
        return False, f"{type(exc).__name__}: {exc}"
    return True, f"{len(bars)} SIP bars in the last 10 minutes (market may be closed if 0, which still counts as entitled)"


def check_opra(config) -> tuple[bool, str]:
    from alpaca.data.enums import OptionsFeed
    from alpaca.data.requests import OptionLatestQuoteRequest, StockLatestTradeRequest

    broker = AlpacaBroker(config)
    from alpaca.data.historical import StockHistoricalDataClient

    stocks = StockHistoricalDataClient(api_key=config.alpaca.api_key_id, secret_key=config.alpaca.api_secret_key)
    try:
        price = float(stocks.get_stock_latest_trade(StockLatestTradeRequest(symbol_or_symbols="SPY"))["SPY"].price)
    except Exception as exc:  # noqa: BLE001
        return False, f"could not read SPY price: {type(exc).__name__}: {exc}"
    contract = None
    day = date.today()
    for _ in range(7):
        if day.weekday() < 5:
            try:
                contract = broker.find_contract("SPY", day, "call", round(price))
            except Exception as exc:  # noqa: BLE001
                return False, f"contract lookup failed: {type(exc).__name__}: {exc}"
            if contract is not None:
                break
        day += timedelta(days=1)
    if contract is None:
        return False, f"no SPY call contract found near {round(price)} in the next week"
    try:
        quotes = broker.option_data.get_option_latest_quote(OptionLatestQuoteRequest(symbol_or_symbols=[contract.occ_symbol], feed=OptionsFeed.OPRA))
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"
    raw = quotes.get(contract.occ_symbol) if isinstance(quotes, dict) else None
    if raw is None:
        return False, f"OPRA returned no quote for {contract.occ_symbol}"
    return True, f"OPRA quote for {contract.occ_symbol}: bid {float(raw.bid_price):.2f} ask {float(raw.ask_price):.2f}"


def apply_switch(config_path: Path) -> list[str]:
    text = config_path.read_text(encoding="utf-8")
    changes = []
    for pattern, replacement, label in (
        (r'^(live_feed\s*=\s*)"iex"', r'\1"sip"', 'live_feed = "sip"'),
        (r'^(option_feed\s*=\s*)"indicative"', r'\1"opra"', 'option_feed = "opra"'),
    ):
        text, count = re.subn(pattern, replacement, text, count=1, flags=re.MULTILINE)
        if count:
            changes.append(label)
    config_path.write_text(text, encoding="utf-8")
    return changes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", default="config.toml")
    parser.add_argument("--apply", action="store_true", help="switch config.toml to SIP and OPRA when both checks pass")
    args = parser.parse_args()
    config_path = Path(args.config)
    config = load_config(config_path)
    print(f"current feeds: live_feed={config.alpaca.live_feed} historical_feed={config.alpaca.historical_feed} option_feed={config.trading.option_feed}")

    sip_ok, sip_msg = check_sip(config)
    print(f"[{'PASS' if sip_ok else 'FAIL'}] real-time SIP stock bars: {sip_msg}")
    opra_ok, opra_msg = check_opra(config)
    print(f"[{'PASS' if opra_ok else 'FAIL'}] OPRA option quotes: {opra_msg}")

    if not (sip_ok and opra_ok):
        print("Not entitled yet; config.toml unchanged.")
        return 1
    if args.apply:
        changes = apply_switch(config_path)
        reloaded = load_config(config_path)
        print(f"config.toml updated: {', '.join(changes) or 'nothing to change (already switched)'}")
        print(f"now: live_feed={reloaded.alpaca.live_feed} option_feed={reloaded.trading.option_feed}")
    else:
        print("Both feeds entitled. Re-run with --apply to switch config.toml.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
