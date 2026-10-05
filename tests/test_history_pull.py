import csv
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from src.backtest import HistoryPuller, find_cross_episodes, occ_symbol, select_strikes
from src.backtest.history import split_by_market_date
from src.config.settings import AlpacaConfig, AppConfig, AppSection, ConfirmationConfig, IndicatorConfig, VolumeConfig
from src.models import Candle, Direction, Timeframe


def _config() -> AppConfig:
    return AppConfig(
        app=AppSection(symbols=["QQQ"], market_timezone="America/New_York"),
        indicators=IndicatorConfig(ema_length=3, sma_fast_length=2, sma_slow_length=3, rvgi_length=2, rvgi_signal_length=2),
        volume=VolumeConfig(),
        confirmation=ConfirmationConfig(enable_one_min_confirmation=False, require_one_min_confirmation=False),
        alpaca=AlpacaConfig(api_key_id="key", api_secret_key="secret"),
    )


def _cross_day(start: datetime) -> list[Candle]:
    closes = [10.0] * 5 + [9.0] * 5 + [8.0] * 5 + [12.0 + 0.1 * index for index in range(20)]
    return [
        Candle("QQQ", Timeframe.ONE_MINUTE, start + timedelta(minutes=index), close - 0.1, close + 0.2, close - 0.2, close, 1000 + index)
        for index, close in enumerate(closes)
    ]


class _FakeAdapter:
    def __init__(self, candles: list[Candle], option_bars: dict[str, list[SimpleNamespace]] | None = None) -> None:
        self.candles = candles
        self.option_bars = option_bars or {}
        self.stock_requests: list[dict[str, object]] = []
        self.option_requests: list[dict[str, object]] = []

    def get_historical_candles(self, symbol, timeframe, start, end, feed=None):
        self.stock_requests.append({"symbol": symbol, "start": start, "end": end, "feed": feed})
        return [candle for candle in self.candles if start <= candle.timestamp < end]

    def get_option_bars(self, occ_symbols, start, end, timeframe=Timeframe.ONE_MINUTE):
        self.option_requests.append({"symbols": list(occ_symbols), "start": start, "end": end})
        return {symbol: bars for symbol, bars in self.option_bars.items() if symbol in occ_symbols}


def test_occ_symbol_matches_alpaca_format():
    assert occ_symbol("qqq", date(2026, 3, 24), "call", 585) == "QQQ260324C00585000"
    assert occ_symbol("QQQ", date(2026, 10, 2), "put", 749.5) == "QQQ261002P00749500"


def test_select_strikes_puts_itm_on_the_correct_side():
    calls = select_strikes("QQQ", date(2026, 3, 24), Direction.BULL, 584.6)
    puts = select_strikes("QQQ", date(2026, 3, 24), Direction.BEAR, 584.6)

    assert [(choice.label, choice.strike) for choice in calls] == [("ITM2", 583.0), ("ITM1", 584.0), ("ATM", 585.0), ("OTM1", 586.0), ("OTM2", 587.0)]
    assert [(choice.label, choice.strike) for choice in puts] == [("ITM2", 587.0), ("ITM1", 586.0), ("ATM", 585.0), ("OTM1", 584.0), ("OTM2", 583.0)]
    assert all(choice.option_type == "call" for choice in calls)
    assert all(choice.option_type == "put" for choice in puts)
    assert calls[2].occ_symbol == "QQQ260324C00585000"


def test_split_by_market_date_uses_eastern_dates():
    late_evening = datetime(2026, 3, 25, 2, 30, tzinfo=UTC)  # 22:30 ET on March 24
    next_morning = datetime(2026, 3, 25, 12, 0, tzinfo=UTC)  # 08:00 ET on March 25
    candles = [
        Candle("QQQ", Timeframe.ONE_MINUTE, next_morning, 1, 1, 1, 1, 1),
        Candle("QQQ", Timeframe.ONE_MINUTE, late_evening, 1, 1, 1, 1, 1),
    ]

    by_day = split_by_market_date(candles, ZoneInfo("America/New_York"))

    assert list(by_day) == [date(2026, 3, 24), date(2026, 3, 25)]


def test_find_cross_episodes_returns_one_episode_per_regular_hours_cross():
    start = datetime(2026, 3, 24, 13, 30, tzinfo=UTC)

    episodes = find_cross_episodes(_cross_day(start), _config())

    assert len(episodes) == 1
    episode = episodes[0]
    assert episode.direction == Direction.BULL
    assert episode.detection_time == datetime(2026, 3, 24, 13, 45, tzinfo=UTC)
    assert episode.spot == 12.0
    assert [choice.occ_symbol for choice in episode.strikes] == [
        "QQQ260324C00010000",
        "QQQ260324C00011000",
        "QQQ260324C00012000",
        "QQQ260324C00013000",
        "QQQ260324C00014000",
    ]


def test_find_cross_episodes_ignores_premarket_crosses():
    start = datetime(2026, 3, 24, 9, 0, tzinfo=UTC)  # 05:00 ET

    assert find_cross_episodes(_cross_day(start), _config()) == []


def test_pull_underlying_writes_one_file_per_day_and_skips_existing(tmp_path: Path):
    day_one = _cross_day(datetime(2026, 3, 24, 13, 30, tzinfo=UTC))
    day_two = _cross_day(datetime(2026, 3, 25, 13, 30, tzinfo=UTC))
    adapter = _FakeAdapter(day_one + day_two)
    messages: list[str] = []
    puller = HistoryPuller(_config(), adapter, tmp_path, printer=messages.append, sleeper=lambda _: None)

    written = puller.pull_underlying("QQQ", date(2026, 3, 24), date(2026, 3, 25))

    assert [path.name for path in written] == ["QQQ_1minute_2026-03-24.csv", "QQQ_1minute_2026-03-25.csv"]
    with written[0].open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert rows[0] == {"timestamp": "2026-03-24T13:30:00+00:00", "open": "9.9", "high": "10.2", "low": "9.8", "close": "10.0", "volume": "1000", "symbol": "QQQ"}
    assert adapter.stock_requests[0]["feed"] == "sip"
    assert adapter.stock_requests[0]["start"] == datetime(2026, 3, 24, 0, 0, tzinfo=ZoneInfo("America/New_York"))

    again = puller.pull_underlying("QQQ", date(2026, 3, 24), date(2026, 3, 25))

    assert again == []
    assert len(adapter.stock_requests) == 1
    assert any("already on disk" in message for message in messages)


def test_pull_options_writes_episode_manifest_and_contract_bars(tmp_path: Path):
    start = datetime(2026, 3, 24, 13, 30, tzinfo=UTC)
    bar_time = datetime(2026, 3, 24, 13, 45, tzinfo=UTC)
    option_bars = {
        "QQQ260324C00012000": [
            SimpleNamespace(timestamp=bar_time, open=1.0, high=1.4, low=0.9, close=1.3, volume=50, trade_count=7, vwap=1.2),
            SimpleNamespace(timestamp=bar_time + timedelta(minutes=1), open=1.3, high=1.6, low=1.2, close=1.5, volume=40, trade_count=5, vwap=1.4),
        ],
        "QQQ260324C00013000": [
            SimpleNamespace(timestamp=bar_time, open=0.4, high=0.6, low=0.3, close=0.5, volume=30, trade_count=3, vwap=0.5),
        ],
    }
    adapter = _FakeAdapter(_cross_day(start), option_bars)
    puller = HistoryPuller(_config(), adapter, tmp_path, printer=lambda _: None, sleeper=lambda _: None)
    puller.pull_underlying("QQQ", date(2026, 3, 24), date(2026, 3, 24))

    written = puller.pull_options("QQQ", date(2026, 3, 24), date(2026, 3, 24))

    assert [path.name for path in written] == ["QQQ_options_2026-03-24.csv"]
    with (tmp_path / "episodes" / "QQQ" / "QQQ_episodes_2026-03-24.csv").open(encoding="utf-8", newline="") as handle:
        episodes = list(csv.DictReader(handle))
    assert len(episodes) == 1
    assert episodes[0]["direction"] == "bull"
    assert episodes[0]["detection_time"] == "2026-03-24T13:45:00+00:00"
    assert episodes[0]["atm_symbol"] == "QQQ260324C00012000"
    assert episodes[0]["otm1_symbol"] == "QQQ260324C00013000"

    with written[0].open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert [(row["occ_symbol"], row["timestamp"], row["close"]) for row in rows] == [
        ("QQQ260324C00012000", "2026-03-24T13:45:00+00:00", "1.3"),
        ("QQQ260324C00012000", "2026-03-24T13:46:00+00:00", "1.5"),
        ("QQQ260324C00013000", "2026-03-24T13:45:00+00:00", "0.5"),
    ]
    assert rows[0]["strike"] == "12.0" and rows[0]["option_type"] == "call"

    request = adapter.option_requests[0]
    assert len(request["symbols"]) == 5
    assert request["start"] == bar_time - timedelta(minutes=15)
    assert request["end"] == datetime(2026, 3, 24, 16, 15, tzinfo=ZoneInfo("America/New_York"))

    assert puller.pull_options("QQQ", date(2026, 3, 24), date(2026, 3, 24)) == []
    assert len(adapter.option_requests) == 1
