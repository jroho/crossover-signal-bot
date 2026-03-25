from datetime import UTC, datetime
from pathlib import Path

from src.backtest import ReplayEngine
from src.main import build_parser


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


def test_replay_parser_accepts_market_flag():
    parser = build_parser()
    args = parser.parse_args(["replay", "--market"])

    assert args.command == "replay"
    assert args.market_hours_only is True


def test_replay_market_filter_keeps_only_market_window(base_config, tmp_path: Path):
    csv_path = tmp_path / "replay_market_window.csv"
    csv_path.write_text(
        "\n".join(
            [
                "timestamp,open,high,low,close,volume,symbol",
                "2026-03-24T13:29:00+00:00,100,101,99,100.5,1000,QQQ",
                "2026-03-24T13:30:00+00:00,101,102,100,101.5,1100,QQQ",
                "2026-03-24T19:45:00+00:00,102,103,101,102.5,1200,QQQ",
                "2026-03-24T19:46:00+00:00,103,104,102,103.5,1300,QQQ",
            ]
        ),
        encoding="utf-8",
    )

    engine = ReplayEngine(base_config)
    candles = engine.adapter.load_candles(str(csv_path), ["QQQ"])
    filtered = engine._filter_market_hours(candles)

    assert [candle.timestamp for candle in filtered] == [
        datetime(2026, 3, 24, 13, 30, tzinfo=UTC),
        datetime(2026, 3, 24, 19, 45, tzinfo=UTC),
    ]


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
