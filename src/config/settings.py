from __future__ import annotations

import os
import tomllib
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class IndicatorConfig:
    ema_length: int = 9
    sma_fast_length: int = 15
    sma_slow_length: int = 30
    rvgi_length: int = 10
    rvgi_signal_length: int = 10


@dataclass(frozen=True)
class VolumeConfig:
    prior_window: int = 5
    use_rolling_average: bool = True
    rolling_window: int = 10
    strong_ratio: float = 1.2
    acceptable_ratio: float = 0.9
    top_n_strong: int = 2


@dataclass(frozen=True)
class ConfirmationConfig:
    enable_one_min_confirmation: bool = True
    require_one_min_confirmation: bool = False


@dataclass(frozen=True)
class GradingConfig:
    alert_grades: list[str] = field(default_factory=lambda: ["A", "B"])
    allow_grade_c_soft_alerts: bool = False
    allow_grade_b_itm: bool = True
    allow_grade_a_otm: bool = True
    allow_two_otm: bool = False


@dataclass(frozen=True)
class StorageConfig:
    sqlite_path: str = "logs/signals.sqlite3"
    evaluation_csv_path: str = "logs/evaluations.csv"
    alert_csv_path: str = "logs/alerts.csv"


@dataclass(frozen=True)
class ReplayConfig:
    csv_path: str = "tests/fixtures/sample_intraday.csv"
    send_telegram: bool = False
    export_csv_path: str = ""


@dataclass(frozen=True)
class TelegramConfig:
    enabled: bool = False
    bot_token: str = ""
    chat_id: str = ""


@dataclass(frozen=True)
class PolygonConfig:
    enabled: bool = False
    base_url: str = "https://api.polygon.io"
    api_key: str = ""


@dataclass(frozen=True)
class LiveConfig:
    lookback_minutes: int = 180
    poll_seconds: int = 60
    market_open_time: str = "09:30"
    market_close_time: str = "15:45"


@dataclass(frozen=True)
class AppSection:
    symbols: list[str] = field(default_factory=lambda: ["QQQ", "SPY"])
    market_timezone: str = "America/New_York"


@dataclass(frozen=True)
class AppConfig:
    app: AppSection = field(default_factory=AppSection)
    indicators: IndicatorConfig = field(default_factory=IndicatorConfig)
    volume: VolumeConfig = field(default_factory=VolumeConfig)
    confirmation: ConfirmationConfig = field(default_factory=ConfirmationConfig)
    grading: GradingConfig = field(default_factory=GradingConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    replay: ReplayConfig = field(default_factory=ReplayConfig)
    telegram: TelegramConfig = field(default_factory=TelegramConfig)
    polygon: PolygonConfig = field(default_factory=PolygonConfig)
    live: LiveConfig = field(default_factory=LiveConfig)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _build_dataclass(cls: type[Any], payload: dict[str, Any] | None) -> Any:
    payload = payload or {}
    return cls(**payload)


def load_config(path: str | Path | None = None) -> AppConfig:
    raw: dict[str, Any] = {}
    if path:
        with Path(path).open("rb") as handle:
            raw = tomllib.load(handle)

    telegram = {**raw.get("telegram", {})}
    polygon = {**raw.get("polygon", {})}
    live = {**raw.get("live", {})}

    telegram["bot_token"] = os.getenv("TELEGRAM_BOT_TOKEN", telegram.get("bot_token", ""))
    telegram["chat_id"] = os.getenv("TELEGRAM_CHAT_ID", telegram.get("chat_id", ""))
    polygon["api_key"] = os.getenv("POLYGON_API_KEY", polygon.get("api_key", ""))

    # Keep loading older config files gracefully even though alert gating is now always market-hours-only.
    live.pop("market_hours_only", None)

    return AppConfig(
        app=_build_dataclass(AppSection, raw.get("app")),
        indicators=_build_dataclass(IndicatorConfig, raw.get("indicators")),
        volume=_build_dataclass(VolumeConfig, raw.get("volume")),
        confirmation=_build_dataclass(ConfirmationConfig, raw.get("confirmation")),
        grading=_build_dataclass(GradingConfig, raw.get("grading")),
        storage=_build_dataclass(StorageConfig, raw.get("storage")),
        replay=_build_dataclass(ReplayConfig, raw.get("replay")),
        telegram=_build_dataclass(TelegramConfig, telegram),
        polygon=_build_dataclass(PolygonConfig, polygon),
        live=_build_dataclass(LiveConfig, live),
    )
