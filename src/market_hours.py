from __future__ import annotations

from datetime import UTC, datetime, time as clock_time
from zoneinfo import ZoneInfo


def parse_clock_time(value: str, field_name: str) -> clock_time:
    try:
        return datetime.strptime(value, "%H:%M").time()
    except ValueError as exc:
        raise SystemExit(f"Invalid {field_name} value '{value}'. Expected HH:MM in 24-hour time.") from exc


def is_within_market_hours(
    now: datetime,
    market_timezone: ZoneInfo,
    market_open: clock_time,
    market_close: clock_time,
) -> bool:
    local_now = now.astimezone(market_timezone)
    if local_now.weekday() >= 5:
        return False

    current_minutes = (local_now.hour * 60) + local_now.minute
    open_minutes = (market_open.hour * 60) + market_open.minute
    close_minutes = (market_close.hour * 60) + market_close.minute
    return open_minutes <= current_minutes <= close_minutes


def filter_market_hours(
    timestamps: list[datetime],
    market_timezone: ZoneInfo,
    market_open: clock_time,
    market_close: clock_time,
) -> list[datetime]:
    return [
        timestamp
        for timestamp in timestamps
        if is_within_market_hours(timestamp.astimezone(UTC), market_timezone, market_open, market_close)
    ]
