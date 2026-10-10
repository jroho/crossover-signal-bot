"""Exit 0 when today is a US equity trading day according to Alpaca's calendar, 1 when it is not.

If the calendar cannot be reached the script exits 0 with a warning, so a network blip does not silently skip a
session; the trade loop's own preflight is the real gate.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.config import load_config  # noqa: E402
from src.execution.broker import AlpacaBroker  # noqa: E402


def main() -> int:
    from alpaca.trading.requests import GetCalendarRequest

    today = date.today()
    if today.weekday() >= 5:
        print(f"{today} is a weekend; no session.")
        return 1
    try:
        broker = AlpacaBroker(load_config(ROOT / "config.toml"))
        days = broker.trading.get_calendar(GetCalendarRequest(start=today, end=today))
    except Exception as exc:  # noqa: BLE001 - do not skip a session over a calendar lookup failure
        print(f"calendar lookup failed ({type(exc).__name__}: {exc}); assuming a trading day.")
        return 0
    if not days:
        print(f"{today} is a market holiday; no session.")
        return 1
    print(f"{today} is a trading day (open {days[0].open:%H:%M} close {days[0].close:%H:%M} ET).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
