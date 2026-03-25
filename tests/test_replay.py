from datetime import UTC, datetime
from pathlib import Path

import pytest

from src.backtest import ReplayEngine
from src.main import build_parser
from src.models import Direction, Grade, SetupEvaluation, StrikeBias, Timeframe


def _evaluation(timestamp: datetime) -> SetupEvaluation:
    return SetupEvaluation(
        symbol="QQQ",
        timestamp=timestamp,
        timeframe=Timeframe.FIVE_MINUTE,
        direction=Direction.BULL,
        last_price=505.0,
        vwap_relation="above",
        ema9_relation="above",
        sma15_value=504.0,
        sma30_value=502.0,
        sma_trend_relation="bullish",
        sma_cross_signal="bull",
        sma_cross_status="active",
        sma_cross_time=timestamp,
        sma15_slope=0.3,
        sma30_slope=0.1,
        rvgi=0.4,
        rvgi_sma=0.2,
        rvgi_vs_sma="above",
        rvgi_sign="positive",
        volume=2500,
        recent_volume_avg=1800,
        rolling_volume_avg=1700,
        volume_grade="strong",
        one_min_agreement="yes",
        grade=Grade.A,
        strike_bias=StrikeBias.ATM,
        strike_bias_reason="default",
    )


def test_replay_is_deterministic(base_config, sample_csv_path, tmp_path: Path):
    export_one = tmp_path / "replay_one.csv"
    export_two = tmp_path / "replay_two.csv"

    engine_one = ReplayEngine(base_config)
    result_one = engine_one.run(csv_path=str(sample_csv_path), export_path=str(export_one))

    second_config = type(base_config)(
        app=base_config.app,
        indicators=base_config.indicators,
        volume=base_config.volume,
        confirmation=base_config.confirmation,
        grading=base_config.grading,
        storage=type(base_config.storage)(
            sqlite_path=str(tmp_path / "signals_second.sqlite3"),
            evaluation_csv_path=str(tmp_path / "evaluations_second.csv"),
            alert_csv_path=str(tmp_path / "alerts_second.csv"),
        ),
        replay=base_config.replay,
        telegram=base_config.telegram,
        polygon=base_config.polygon,
        live=base_config.live,
    )
    engine_two = ReplayEngine(second_config)
    result_two = engine_two.run(csv_path=str(sample_csv_path), export_path=str(export_two))

    assert [item.to_record() for item in result_one.evaluations] == [item.to_record() for item in result_two.evaluations]
    assert [alert.payload.message for alert in result_one.alerts] == [alert.payload.message for alert in result_two.alerts]
    assert export_one.read_text(encoding="utf-8") == export_two.read_text(encoding="utf-8")
    assert any(item.grade.value == "A" for item in result_one.evaluations)
    assert any(item.direction.value == "bear" and item.grade.value in {"A", "B"} for item in result_one.evaluations)
    assert any(item.forward_return_30m is not None for item in result_one.evaluations)
    assert any(item.pop_outcome is not None for item in result_one.evaluations)


def test_replay_parser_rejects_market_flag():
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["replay", "--market"])


def test_replay_keeps_premarket_evaluations_but_alerts_only_market_hours(base_config, monkeypatch):
    engine = ReplayEngine(base_config)
    monkeypatch.setattr(engine.adapter, "load_candles", lambda path, symbols: [])
    premarket = _evaluation(datetime(2026, 3, 24, 13, 29, tzinfo=UTC))
    market = _evaluation(datetime(2026, 3, 24, 13, 30, tzinfo=UTC))
    monkeypatch.setattr("src.backtest.replay.evaluate_symbol", lambda candles, config: ([premarket, market], None, None))

    result = engine.run(csv_path="ignored.csv")

    assert [item.timestamp for item in result.evaluations] == [premarket.timestamp, market.timestamp]
    assert [item.evaluation.timestamp for item in result.alerts] == [market.timestamp]


def test_replay_resolves_legacy_five_minute_fixture_name(base_config, tmp_path: Path):
    actual_path = tmp_path / "QQQ_1minute_2026-03-24.csv"
    requested_path = tmp_path / "QQQ_5minute_2026-03-24.csv"
    actual_path.write_text(
        "\n".join(
            [
                "timestamp,open,high,low,close,volume,symbol",
                "2026-03-24T13:30:00+00:00,100,101,99,100.5,1000,QQQ",
                "2026-03-24T13:31:00+00:00,101,102,100,101.5,1100,QQQ",
            ]
        ),
        encoding="utf-8",
    )

    engine = ReplayEngine(base_config)
    resolved = engine._resolve_source_path(requested_path)

    assert resolved == actual_path

