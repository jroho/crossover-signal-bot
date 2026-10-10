"""Print the [schedule] section of config.toml as key=value lines for the PowerShell scheduler scripts.

Times in config are Eastern (market time); start_local / stop_local are the same clock times converted to this
machine's time zone for Task Scheduler, which only understands local time. Missing keys fall back to defaults.
"""

from __future__ import annotations

import sys
import tomllib
from datetime import date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

DEFAULTS = {
    "enabled": True,
    "mode": "paper",  # paper or dry-run
    "bias": "",  # testing override only; empty = data bias
    "start_time": "09:20",  # ET, loop starts and waits for the 09:45 entry window
    "stop_time": "15:40",  # ET, after the 15:35 flatten; stops the loop and runs the parity check
    "days": "MON,TUE,WED,THU,FRI",
}


def load(config_path: Path) -> dict[str, object]:
    with config_path.open("rb") as handle:
        raw = tomllib.load(handle)
    section = raw.get("schedule", {})
    settings = {**DEFAULTS, **{key: section[key] for key in DEFAULTS if key in section}}
    if settings["mode"] not in {"paper", "dry-run"}:
        raise SystemExit(f"[schedule] mode must be paper or dry-run, not {settings['mode']!r}")
    if settings["bias"] not in {"", "bull", "bear", "neutral"}:
        raise SystemExit(f"[schedule] bias must be empty, bull, bear or neutral, not {settings['bias']!r}")
    market = ZoneInfo(raw.get("app", {}).get("market_timezone", "America/New_York"))
    for key in ("start_time", "stop_time"):
        clock = time.fromisoformat(str(settings[key]))
        stamp = datetime.combine(date.today(), clock, tzinfo=market).astimezone()
        settings[key.replace("_time", "_local")] = stamp.strftime("%H:%M")
    return settings


def main() -> int:
    config_path = Path(sys.argv[1] if len(sys.argv) > 1 else "config.toml")
    for key, value in load(config_path).items():
        text = str(value).lower() if isinstance(value, bool) else str(value)
        print(f"{key}={text}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
