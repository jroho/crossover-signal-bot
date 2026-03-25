from pathlib import Path

import pytest

from src.config.settings import (
    AppConfig,
    AppSection,
    ConfirmationConfig,
    GradingConfig,
    IndicatorConfig,
    LiveConfig,
    PolygonConfig,
    ReplayConfig,
    StorageConfig,
    TelegramConfig,
    VolumeConfig,
)


@pytest.fixture()
def sample_csv_path() -> Path:
    return Path("tests/fixtures/sample_intraday.csv")


@pytest.fixture()
def base_config(tmp_path: Path, sample_csv_path: Path) -> AppConfig:
    return AppConfig(
        app=AppSection(symbols=["QQQ", "SPY"], market_timezone="America/New_York"),
        indicators=IndicatorConfig(),
        volume=VolumeConfig(),
        confirmation=ConfirmationConfig(enable_one_min_confirmation=True, require_one_min_confirmation=False),
        grading=GradingConfig(alert_grades=["A", "B"], allow_grade_c_soft_alerts=False, allow_grade_b_itm=True, allow_grade_a_otm=True, allow_two_otm=False),
        storage=StorageConfig(
            sqlite_path=str(tmp_path / "signals.sqlite3"),
            evaluation_csv_path=str(tmp_path / "evaluations.csv"),
            alert_csv_path=str(tmp_path / "alerts.csv"),
        ),
        replay=ReplayConfig(csv_path=str(sample_csv_path), send_telegram=False, export_csv_path=""),
        telegram=TelegramConfig(enabled=False, bot_token="", chat_id=""),
        polygon=PolygonConfig(enabled=False, base_url="https://api.polygon.io", api_key=""),
        live=LiveConfig(lookback_minutes=180, poll_seconds=60),
    )
