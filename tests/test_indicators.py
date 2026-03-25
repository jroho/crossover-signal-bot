from datetime import UTC, datetime, timedelta

from src.config.settings import AppConfig, AppSection, ConfirmationConfig, GradingConfig, IndicatorConfig, LiveConfig, PolygonConfig, ReplayConfig, StorageConfig, TelegramConfig, VolumeConfig
from src.data import CsvReplayAdapter
from src.indicators import build_indicator_bundle, resample_to_active_five_minute, resample_to_five_minute
from src.models import Candle, Timeframe
from src.signals import evaluate_symbol


def _intrabar_config() -> AppConfig:
    return AppConfig(
        app=AppSection(symbols=["QQQ"], market_timezone="America/New_York"),
        indicators=IndicatorConfig(ema_length=3, sma_fast_length=2, sma_slow_length=3, rvgi_length=2, rvgi_signal_length=2),
        volume=VolumeConfig(),
        confirmation=ConfirmationConfig(enable_one_min_confirmation=False, require_one_min_confirmation=False),
        grading=GradingConfig(alert_grades=["A", "B"], allow_grade_c_soft_alerts=False, allow_grade_b_itm=True, allow_grade_a_otm=True, allow_two_otm=False),
        storage=StorageConfig(),
        replay=ReplayConfig(),
        telegram=TelegramConfig(),
        polygon=PolygonConfig(),
        live=LiveConfig(),
    )


def test_vwap_resets_at_new_session(base_config, sample_csv_path):
    candles = CsvReplayAdapter().load_candles(sample_csv_path, ["QQQ"])
    one_min_bundle, _ = build_indicator_bundle(candles, base_config)
    frame = one_min_bundle.dataframe[one_min_bundle.dataframe["symbol"] == "QQQ"].copy()

    session_starts = frame.groupby(frame["timestamp"].dt.date).head(1).reset_index(drop=True)
    assert len(session_starts) == 2
    first_row = session_starts.iloc[0]
    second_row = session_starts.iloc[1]

    first_typical = (first_row["high"] + first_row["low"] + first_row["close"]) / 3.0
    second_typical = (second_row["high"] + second_row["low"] + second_row["close"]) / 3.0

    assert abs(first_row["vwap"] - first_typical) < 1e-9
    assert abs(second_row["vwap"] - second_typical) < 1e-9
    assert abs(second_row["vwap"] - frame.iloc[0]["vwap"]) > 1.0


def test_resample_to_five_minute_uses_left_closed_windows():
    candles = [
        Candle("QQQ", Timeframe.ONE_MINUTE, datetime(2026, 3, 24, 13, 30, tzinfo=UTC), 100, 100, 100, 100, 10),
        Candle("QQQ", Timeframe.ONE_MINUTE, datetime(2026, 3, 24, 13, 31, tzinfo=UTC), 101, 101, 101, 101, 11),
        Candle("QQQ", Timeframe.ONE_MINUTE, datetime(2026, 3, 24, 13, 32, tzinfo=UTC), 102, 102, 102, 102, 12),
        Candle("QQQ", Timeframe.ONE_MINUTE, datetime(2026, 3, 24, 13, 33, tzinfo=UTC), 103, 103, 103, 103, 13),
        Candle("QQQ", Timeframe.ONE_MINUTE, datetime(2026, 3, 24, 13, 34, tzinfo=UTC), 104, 104, 104, 104, 14),
        Candle("QQQ", Timeframe.ONE_MINUTE, datetime(2026, 3, 24, 13, 35, tzinfo=UTC), 105, 105, 105, 105, 15),
        Candle("QQQ", Timeframe.ONE_MINUTE, datetime(2026, 3, 24, 13, 36, tzinfo=UTC), 106, 106, 106, 106, 16),
        Candle("QQQ", Timeframe.ONE_MINUTE, datetime(2026, 3, 24, 13, 37, tzinfo=UTC), 107, 107, 107, 107, 17),
        Candle("QQQ", Timeframe.ONE_MINUTE, datetime(2026, 3, 24, 13, 38, tzinfo=UTC), 108, 108, 108, 108, 18),
        Candle("QQQ", Timeframe.ONE_MINUTE, datetime(2026, 3, 24, 13, 39, tzinfo=UTC), 109, 109, 109, 109, 19),
    ]

    five_minute = resample_to_five_minute(candles)

    assert len(five_minute) == 2
    assert five_minute[0].timestamp == datetime(2026, 3, 24, 13, 35, tzinfo=UTC)
    assert five_minute[0].open == 100.0
    assert five_minute[0].close == 104.0
    assert five_minute[0].volume == 60.0
    assert five_minute[1].timestamp == datetime(2026, 3, 24, 13, 40, tzinfo=UTC)
    assert five_minute[1].open == 105.0
    assert five_minute[1].close == 109.0
    assert five_minute[1].volume == 85.0


def test_resample_to_active_five_minute_updates_last_snapshot_every_minute():
    candles = [
        Candle("QQQ", Timeframe.ONE_MINUTE, datetime(2026, 3, 24, 13, 30, tzinfo=UTC), 100, 100, 99, 100, 10),
        Candle("QQQ", Timeframe.ONE_MINUTE, datetime(2026, 3, 24, 13, 31, tzinfo=UTC), 101, 101, 100, 101, 11),
        Candle("QQQ", Timeframe.ONE_MINUTE, datetime(2026, 3, 24, 13, 32, tzinfo=UTC), 102, 102, 101, 102, 12),
        Candle("QQQ", Timeframe.ONE_MINUTE, datetime(2026, 3, 24, 13, 33, tzinfo=UTC), 103, 103, 102, 103, 13),
        Candle("QQQ", Timeframe.ONE_MINUTE, datetime(2026, 3, 24, 13, 34, tzinfo=UTC), 104, 104, 103, 104, 14),
        Candle("QQQ", Timeframe.ONE_MINUTE, datetime(2026, 3, 24, 13, 35, tzinfo=UTC), 105, 105, 104, 105, 15),
    ]

    active_five_minute = resample_to_active_five_minute(candles)

    assert len(active_five_minute) == 2
    assert active_five_minute[0].timestamp == datetime(2026, 3, 24, 13, 34, tzinfo=UTC)
    assert active_five_minute[0].close == 104.0
    assert active_five_minute[1].timestamp == datetime(2026, 3, 24, 13, 35, tzinfo=UTC)
    assert active_five_minute[1].open == 105.0
    assert active_five_minute[1].close == 105.0
    assert active_five_minute[1].volume == 15.0


def test_evaluate_symbol_detects_intrabar_five_min_cross_on_minute_updates():
    start = datetime(2026, 3, 24, 13, 30, tzinfo=UTC)
    closes = [10.0] * 5 + [9.0] * 5 + [8.0] * 5 + [12.0]
    candles = []
    for index, close in enumerate(closes):
        timestamp = start + timedelta(minutes=index)
        candles.append(
            Candle(
                "QQQ",
                Timeframe.ONE_MINUTE,
                timestamp,
                close - 0.1,
                close + 0.2,
                close - 0.2,
                close,
                1000 + index,
            )
        )

    evaluations, _, _ = evaluate_symbol(candles, _intrabar_config())
    bull_evaluations = [item for item in evaluations if item.direction.value == "bull"]
    target = next(item for item in bull_evaluations if item.timestamp == datetime(2026, 3, 24, 13, 45, tzinfo=UTC))

    assert target.sma_cross_signal == "bull"
    assert target.sma_cross_status == "fresh"
    assert target.sma_cross_time is not None
    assert datetime(2026, 3, 24, 13, 44, tzinfo=UTC) <= target.sma_cross_time <= datetime(2026, 3, 24, 13, 45, tzinfo=UTC)
