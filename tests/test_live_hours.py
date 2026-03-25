from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from src.main import build_parser
from src.market_hours import is_within_market_hours


CLOCK_0930 = datetime.strptime("09:30", "%H:%M").time()
CLOCK_1545 = datetime.strptime("15:45", "%H:%M").time()


def test_market_hours_gate_uses_new_york_window():
    market_timezone = ZoneInfo("America/New_York")

    assert is_within_market_hours(
        datetime(2026, 3, 24, 13, 29, tzinfo=UTC),
        market_timezone,
        CLOCK_0930,
        CLOCK_1545,
    ) is False
    assert is_within_market_hours(
        datetime(2026, 3, 24, 13, 30, tzinfo=UTC),
        market_timezone,
        CLOCK_0930,
        CLOCK_1545,
    ) is True
    assert is_within_market_hours(
        datetime(2026, 3, 24, 19, 45, tzinfo=UTC),
        market_timezone,
        CLOCK_0930,
        CLOCK_1545,
    ) is True
    assert is_within_market_hours(
        datetime(2026, 3, 24, 19, 46, tzinfo=UTC),
        market_timezone,
        CLOCK_0930,
        CLOCK_1545,
    ) is False


def test_market_hours_gate_skips_weekends():
    market_timezone = ZoneInfo("America/New_York")

    assert is_within_market_hours(
        datetime(2026, 3, 28, 15, 0, tzinfo=UTC),
        market_timezone,
        CLOCK_0930,
        CLOCK_1545,
    ) is False


def test_live_parser_rejects_market_flags():
    parser = build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["live", "--market"])
    with pytest.raises(SystemExit):
        parser.parse_args(["live", "--market-hours-only"])
