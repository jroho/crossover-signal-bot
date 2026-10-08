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
    # A 5m crossover older than this many 5m bars no longer counts as a trigger.
    fresh_cross_max_bars: int = 3
    # A+ additionally requires the crossover to be at most this many 5m bars old.
    a_plus_max_cross_bars: int = 1
    # When false the grade is computed as if trigger volume were always strong; the volume grade is still measured
    # and logged. Exists to test whether the volume filter earns its place, because on the free IEX feed the live
    # volume grade agrees with consolidated SIP volume on only about half of all minutes.
    volume_in_grade: bool = True


@dataclass(frozen=True)
class OutcomeConfig:
    # Underlying move (percent) that counts as a realized pop win or loss.
    pop_threshold_pct: float = 0.17
    pop_grade_b_pct: float = 0.34
    pop_grade_a_pct: float = 0.51


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
class AlpacaConfig:
    enabled: bool = False
    api_key_id: str = ""
    api_secret_key: str = ""
    # Paper vs live only matters for trading endpoints; market data is shared.
    paper: bool = True
    # Consolidated tape for backfills; the free plan withholds its most recent 15 minutes.
    historical_feed: str = "sip"
    # Real-time on the free plan, so it serves the window the historical feed cannot.
    live_feed: str = "iex"
    adjustment: str = "split"
    # Fallback SIP/IEX volume ratio when the seam has no overlap to measure it on.
    iex_volume_scale: float = 40.0


@dataclass(frozen=True)
class DataConfig:
    # "alpaca" or "polygon"; empty selects whichever provider has credentials configured.
    provider: str = ""


@dataclass(frozen=True)
class TradingConfig:
    enabled: bool = False
    paper: bool = True
    # Sizing is against this figure, not the broker's equity, so a $100K paper account behaves like $500.
    risk_capital_usd: float = 500.0
    usd_per_contract: float = 1250.0
    bear_multiplier: int = 2
    max_contracts: int = 3
    max_position_cost_pct: float = 50.0
    max_open_positions: int = 2
    # Each crossover episode is decided once, on its first evaluation graded at least this; if that grade is below
    # min_grade the episode is skipped rather than re-checked for an upgrade (late upgrades lost in the backtest).
    entry_floor_grade: str = "B"
    min_grade: str = "A"
    plain_a_max_lag_min: float | None = 5.0
    require_alignment: bool = True
    # When true, an A+ setup also satisfies the alignment requirement if it is the symbol's first regular-hours
    # crossover of the session, or the other watched symbol crossed the same way within confirmation_window_min.
    # Plain-A setups still need the day's trend bias. Backtest: ~80 trades/yr at 69% vs 55 at 67% aligned-only.
    a_plus_confirmation: bool = False
    confirmation_window_min: float = 10.0
    # Late-follower skip: no entry when the other watched symbol's most recent same-direction regular-hours cross came
    # more than late_follower_min_minutes and at most late_follower_max_minutes before this cross. Backtest (P4+,
    # Oct 2025-Oct 2026, window 5-20): removed 9 trades at 44% win, PF 2.27 -> 2.65, max drawdown -$316 -> -$230, both
    # halves improved. A max of 0 disables the rule.
    late_follower_min_minutes: float = 0.0
    late_follower_max_minutes: float = 0.0
    # Crossovers in the same direction within this many minutes are one episode. The interpolated cross time of a
    # cross on the still-printing 5m bar drifts between polls, and without merging the same cross is re-decided.
    episode_merge_minutes: float = 5.0
    # The bear multiplier applies only to aligned bears at or above this grade ("A" keeps every aligned bear).
    # Backtest: A+ aligned bears won 88% in both halves of the year; plain-A bears did not hold up.
    press_min_grade: str = "A"
    # bull / bear / neutral set before the open from headlines; empty uses the data bias.
    daily_bias_override: str = ""
    # No entries before this market time; the first 15 minutes lost in both the policy and unfiltered backtests.
    first_entry_time: str = ""
    last_entry_time: str = "13:00"
    # Bull entries stop earlier than bears when set; noon-to-one bull entries lost in both backtests.
    bull_last_entry_time: str = ""
    flat_time: str = "15:35"
    max_hold_minutes: int = 45
    target_pct: float = 30.0
    stop_pct: float = 30.0
    entry_limit_buffer: float = 0.02
    entry_timeout_minutes: int = 3
    daily_loss_limit_usd: float = 150.0
    max_losses_per_day: int = 2
    drawdown_kill_usd: float = 200.0
    option_feed: str = "indicative"
    journal_sqlite_path: str = "logs/live_trades.sqlite3"
    journal_csv_path: str = "logs/live_trades.csv"


@dataclass(frozen=True)
class LiveConfig:
    # Legacy sliding window for the Polygon path; Alpaca live/trade modes evaluate from session_start_time.
    lookback_minutes: int = 180
    session_start_time: str = "04:00"
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
    outcomes: OutcomeConfig = field(default_factory=OutcomeConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    replay: ReplayConfig = field(default_factory=ReplayConfig)
    telegram: TelegramConfig = field(default_factory=TelegramConfig)
    polygon: PolygonConfig = field(default_factory=PolygonConfig)
    alpaca: AlpacaConfig = field(default_factory=AlpacaConfig)
    data: DataConfig = field(default_factory=DataConfig)
    trading: TradingConfig = field(default_factory=TradingConfig)
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
    alpaca = {**raw.get("alpaca", {})}
    live = {**raw.get("live", {})}

    telegram["bot_token"] = os.getenv("TELEGRAM_BOT_TOKEN", telegram.get("bot_token", ""))
    telegram["chat_id"] = os.getenv("TELEGRAM_CHAT_ID", telegram.get("chat_id", ""))
    polygon["api_key"] = os.getenv("POLYGON_API_KEY", polygon.get("api_key", ""))
    alpaca["api_key_id"] = os.getenv("APCA_API_KEY_ID", alpaca.get("api_key_id", ""))
    alpaca["api_secret_key"] = os.getenv("APCA_API_SECRET_KEY", alpaca.get("api_secret_key", ""))

    # Keep loading older config files gracefully even though alert gating is now always market-hours-only.
    live.pop("market_hours_only", None)

    return AppConfig(
        app=_build_dataclass(AppSection, raw.get("app")),
        indicators=_build_dataclass(IndicatorConfig, raw.get("indicators")),
        volume=_build_dataclass(VolumeConfig, raw.get("volume")),
        confirmation=_build_dataclass(ConfirmationConfig, raw.get("confirmation")),
        grading=_build_dataclass(GradingConfig, raw.get("grading")),
        outcomes=_build_dataclass(OutcomeConfig, raw.get("outcomes")),
        storage=_build_dataclass(StorageConfig, raw.get("storage")),
        replay=_build_dataclass(ReplayConfig, raw.get("replay")),
        telegram=_build_dataclass(TelegramConfig, telegram),
        polygon=_build_dataclass(PolygonConfig, polygon),
        alpaca=_build_dataclass(AlpacaConfig, alpaca),
        data=_build_dataclass(DataConfig, raw.get("data")),
        trading=_build_dataclass(TradingConfig, raw.get("trading")),
        live=_build_dataclass(LiveConfig, live),
    )
