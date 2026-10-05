from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import pytest
from alpaca.data.enums import Adjustment, DataFeed

from src.config.settings import AlpacaConfig, DataConfig, PolygonConfig, load_config
from src.data import AlpacaAdapter
from src.main import _resolve_provider, main
from src.models import Timeframe


def _as_utc(value: datetime) -> datetime:
    # StockBarsRequest normalizes tz-aware datetimes to naive UTC; compare in aware UTC either way.
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _bar(timestamp: datetime, close: float, volume: float = 1000.0) -> SimpleNamespace:
    return SimpleNamespace(
        timestamp=timestamp,
        open=close - 0.2,
        high=close + 0.3,
        low=close - 0.4,
        close=close,
        volume=volume,
        vwap=close - 0.05,
        trade_count=42,
    )


class _RecordingClient:
    def __init__(self, bars_by_symbol: dict[str, list[SimpleNamespace]]) -> None:
        self.bars_by_symbol = bars_by_symbol
        self.requests: list[object] = []

    def get_stock_bars(self, request: object) -> SimpleNamespace:
        self.requests.append(request)
        return SimpleNamespace(data=self.bars_by_symbol)


@pytest.fixture()
def alpaca_config(base_config):
    return replace(base_config, alpaca=AlpacaConfig(enabled=True, api_key_id="key", api_secret_key="secret"))


def test_historical_candles_use_consolidated_feed_and_sort_bars(alpaca_config):
    first = datetime(2026, 3, 24, 13, 30, tzinfo=UTC)
    second = first + timedelta(minutes=1)
    client = _RecordingClient({"QQQ": [_bar(second, 586.5), _bar(first, 586.1)]})
    adapter = AlpacaAdapter(alpaca_config, client=client)

    candles = adapter.get_historical_candles("qqq", Timeframe.ONE_MINUTE, first, first + timedelta(hours=1))

    assert [candle.timestamp for candle in candles] == [first, second]
    assert candles[0].symbol == "QQQ"
    assert candles[0].timeframe == Timeframe.ONE_MINUTE
    assert (candles[0].open, candles[0].high, candles[0].low, candles[0].close, candles[0].volume) == (
        pytest.approx(585.9),
        pytest.approx(586.4),
        pytest.approx(585.7),
        pytest.approx(586.1),
        1000.0,
    )

    request = client.requests[0]
    assert request.symbol_or_symbols == "QQQ"
    assert request.feed == DataFeed.SIP
    assert request.adjustment == Adjustment.SPLIT
    assert request.timeframe.value == "1Min"
    assert _as_utc(request.start) == first
    assert _as_utc(request.end) == first + timedelta(hours=1)


def test_five_minute_timeframe_maps_to_five_minute_bars(alpaca_config):
    start = datetime(2026, 3, 24, 13, 30, tzinfo=UTC)
    client = _RecordingClient({"QQQ": []})
    adapter = AlpacaAdapter(alpaca_config, client=client)

    adapter.get_historical_candles("QQQ", Timeframe.FIVE_MINUTE, start, start + timedelta(hours=1))

    assert client.requests[0].timeframe.value == "5Min"


def test_recent_window_switches_to_live_feed(alpaca_config):
    client = _RecordingClient({"QQQ": []})
    adapter = AlpacaAdapter(alpaca_config, client=client)
    end = datetime.now(tz=UTC)

    adapter.get_historical_candles("QQQ", Timeframe.ONE_MINUTE, end - timedelta(hours=3), end)

    assert client.requests[0].feed == DataFeed.IEX


def test_select_feed_boundary_is_fifteen_minutes(alpaca_config):
    adapter = AlpacaAdapter(alpaca_config, client=_RecordingClient({}))
    now = datetime(2026, 10, 5, 15, 0, tzinfo=UTC)

    assert adapter.select_feed(now - timedelta(minutes=16), now=now) == "sip"
    assert adapter.select_feed(now - timedelta(minutes=14), now=now) == "iex"


def test_explicit_feed_overrides_window_selection(alpaca_config):
    client = _RecordingClient({"QQQ": []})
    adapter = AlpacaAdapter(alpaca_config, client=client)
    end = datetime.now(tz=UTC)

    adapter.get_historical_candles("QQQ", Timeframe.ONE_MINUTE, end - timedelta(hours=3), end, feed="sip")

    assert client.requests[0].feed == DataFeed.SIP


def test_single_day_aggregate_rows_cover_the_market_day(alpaca_config):
    bar_time = datetime(2026, 3, 24, 14, 30, tzinfo=UTC)
    client = _RecordingClient({"SPY": [_bar(bar_time, 586.24, volume=12500)]})
    adapter = AlpacaAdapter(alpaca_config, client=client)

    rows = adapter.get_single_day_aggregate_rows("spy", date(2026, 3, 24), multiplier=1)

    assert rows == [
        {
            "t": 1774362600000,
            "o": pytest.approx(586.04),
            "h": pytest.approx(586.54),
            "l": pytest.approx(585.84),
            "c": 586.24,
            "v": 12500,
            "vw": pytest.approx(586.19),
            "n": 42,
        }
    ]
    request = client.requests[0]
    # Midnight-to-midnight in America/New_York, expressed in UTC (EDT on this date).
    assert _as_utc(request.start) == datetime(2026, 3, 24, 4, 0, tzinfo=UTC)
    assert _as_utc(request.end) == datetime(2026, 3, 25, 4, 0, tzinfo=UTC)
    assert request.feed == DataFeed.SIP


def test_single_day_rejects_unsupported_multiplier(alpaca_config):
    adapter = AlpacaAdapter(alpaca_config, client=_RecordingClient({}))

    with pytest.raises(ValueError):
        adapter.get_single_day_aggregate_rows("QQQ", date(2026, 3, 24), multiplier=3)


def test_load_config_reads_alpaca_credentials_from_env(monkeypatch, tmp_path):
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
[data]
provider = "alpaca"

[alpaca]
api_key_id = "from-file"
api_secret_key = "from-file"
live_feed = "sip"
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("APCA_API_KEY_ID", "from-env")
    monkeypatch.setenv("APCA_API_SECRET_KEY", "from-env-secret")

    config = load_config(config_path)

    assert config.data.provider == "alpaca"
    assert config.alpaca.api_key_id == "from-env"
    assert config.alpaca.api_secret_key == "from-env-secret"
    assert config.alpaca.live_feed == "sip"
    assert config.alpaca.historical_feed == "sip"


def test_resolve_provider_prefers_explicit_setting_then_credentials(base_config):
    explicit = replace(base_config, data=DataConfig(provider="Polygon"), alpaca=AlpacaConfig(api_key_id="k", api_secret_key="s"))
    assert _resolve_provider(explicit) == "polygon"

    alpaca_only = replace(base_config, alpaca=AlpacaConfig(api_key_id="k", api_secret_key="s"))
    assert _resolve_provider(alpaca_only) == "alpaca"

    polygon_only = replace(base_config, polygon=PolygonConfig(api_key="p"))
    assert _resolve_provider(polygon_only) == "polygon"

    with pytest.raises(SystemExit):
        _resolve_provider(base_config)

    with pytest.raises(SystemExit):
        _resolve_provider(replace(base_config, data=DataConfig(provider="yahoo")))


def test_fetch_day_uses_alpaca_adapter_when_selected(monkeypatch, tmp_path, capsys):
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
[app]
symbols = ["SPY"]

[data]
provider = "alpaca"

[alpaca]
api_key_id = "key"
api_secret_key = "secret"
""".strip(),
        encoding="utf-8",
    )
    output_path = tmp_path / "spy_day.csv"
    captured: dict[str, object] = {}

    class FakeAdapter:
        def __init__(self, config) -> None:
            self.config = config

        def get_single_day_aggregate_rows(self, symbol: str, day: date, multiplier: int) -> list[dict[str, object]]:
            captured.update({"symbol": symbol, "day": day, "multiplier": multiplier})
            return [{"t": 1774362600000, "o": 586.1, "h": 586.4, "l": 585.9, "c": 586.24, "v": 12500, "vw": 586.18, "n": 812}]

    monkeypatch.setattr("src.main.AlpacaAdapter", FakeAdapter)

    main(["--config", str(config_path), "fetch-day", "-date", "2026-03-24", "--output", str(output_path)])

    assert captured == {"symbol": "SPY", "day": date(2026, 3, 24), "multiplier": 1}
    assert output_path.read_text(encoding="utf-8") == (
        "timestamp,open,high,low,close,volume,symbol\n"
        "2026-03-24T14:30:00.0000000+00:00,586.1,586.4,585.9,586.24,12500,SPY\n"
    )
    assert capsys.readouterr().out.strip() == f"Saved 1 rows to {output_path}"


def test_fetch_day_without_any_credentials_exits(tmp_path):
    config_path = tmp_path / "config.toml"
    config_path.write_text('[app]\nsymbols = ["SPY"]\n', encoding="utf-8")

    with pytest.raises(SystemExit):
        main(["--config", str(config_path), "fetch-day", "-date", "2026-03-24"])
