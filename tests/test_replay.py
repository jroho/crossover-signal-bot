from pathlib import Path

from src.backtest import ReplayEngine


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
