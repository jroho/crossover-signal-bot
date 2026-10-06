from __future__ import annotations

import csv
from collections.abc import Callable
from dataclasses import asdict, dataclass, fields
from datetime import date, datetime, time as dt_time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from src.config import AppConfig
from src.data import CsvReplayAdapter
from src.models import GRADE_RANK, Direction, Grade
from src.signals import evaluate_symbol

from .context import OPENING_RANGE_END, DayContext, alignment, build_daily_summary, context_for_day, is_late_follower, load_vix_history
from .strikes import STRIKE_LABELS

LAG_BUCKETS = ((5.0, "<=5"), (15.0, "6-15"), (30.0, "16-30"), (float("inf"), ">30"))
FILL_SEARCH_MINUTES = 3


TIME_RULE_MODES = ("off", "option", "underlying")


@dataclass(frozen=True)
class SimConfig:
    target_pct: float = 50.0
    stop_pct: float = 30.0
    # Dollars per share paid on each side; half a typical 0DTE ATM spread.
    slippage: float = 0.02
    min_grade: str = "A"
    # Give up on an episode if no alertable grade appears within this many minutes of detection.
    max_entry_delay_min: int = 15
    # No new entries once the fill would land after this market time; afternoon crosses lost in every bucket.
    last_entry_time: str | None = "13:00"
    # "off": no time rule. "option": exit at the check if the option is at or below entry and the trend has
    # faded. "underlying": exit at the check whenever the trend checks fail, whatever the option has done.
    time_rule_mode: str = "off"
    check_minutes: int = 15
    recheck_minutes: int = 25
    max_hold_minutes: int = 45
    flat_time: str = "15:35"
    labels: tuple[str, ...] = ("ATM",)
    # Once the option has traded this far above entry, the stop ratchets up to trail the high.
    trail_trigger_pct: float | None = None
    # Distance of the trailing stop below the highest high, in percent of that high.
    trail_pct: float = 15.0
    # Entry filters found to hold out of sample. A plain-A entry is accepted only when the 1m->5m cross lag
    # is at most this many minutes; A+ always qualifies. None disables the lag requirement.
    plain_a_max_lag_min: float | None = None
    # Bull trades only when the day's data trend bias is bullish; bear trades are always allowed.
    bull_requires_alignment: bool = False
    # Both directions must agree with the day's data trend bias; neutral or unknown days take no trades.
    require_alignment: bool = False
    # Skip entries on days whose opening range reads "turbulent" (only known for fills after 9:45).
    skip_turbulent: bool = False
    # Entry window: no fills before first_entry_time; bull fills stop at bull_last_entry_time when set.
    first_entry_time: str | None = None
    bull_last_entry_time: str | None = None
    # An A+ setup passes the alignment filters when it is the symbol's first regular-hours cross of the day or
    # another symbol crossed the same way within confirmation_window_min minutes before the fill.
    a_plus_confirmation: bool = False
    confirmation_window_min: float = 10.0
    # Live-engine parity: decide the entry on the FIRST evaluation graded at least this (the engine's entry floor),
    # and skip the episode when that evaluation is below min_grade instead of waiting for an upgrade. None keeps
    # the older behaviour of waiting up to max_entry_delay_min for min_grade. Upgrades that arrive later lost badly
    # in the backtest (A+ after a lower first grade: 38% win), so parity also happens to be the better rule.
    entry_floor_grade: str | None = None
    # Late-follower skip: decline the entry when the other symbol's most recent same-direction regular-hours cross came
    # more than late_follower_min_minutes and at most late_follower_max_minutes before this cross. Inside a few minutes
    # the indices are moving together; inside the window the laggard is chasing a move the leader already made.
    late_follower_min_minutes: float | None = None
    late_follower_max_minutes: float | None = None


@dataclass(frozen=True)
class OptionBar:
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float


@dataclass
class TradeResult:
    symbol: str
    date: str
    direction: str
    cross_time: str
    detection_time: str
    label: str
    occ_symbol: str
    strike: float | None
    target_pct: float
    stop_pct: float
    exit_reason: str
    trail_trigger_pct: float | None = None
    entry_grade: str | None = None
    entry_time: str | None = None
    entry_delay_min: float | None = None
    entry_price: float | None = None
    exit_time: str | None = None
    exit_price: float | None = None
    hold_minutes: float | None = None
    pnl_usd: float | None = None
    return_pct: float | None = None
    mfe_pct: float | None = None
    mae_pct: float | None = None
    lag_min: float | None = None
    lag_bucket: str = "none"
    entry_hour_et: int | None = None
    entry_time_et: str | None = None
    time_bucket: str | None = None
    day_of_week: str | None = None
    vix_prev_close: float | None = None
    vix_regime: str = "unknown"
    trend_label: str = "unknown"
    alignment: str = "unknown"
    opening_range_ratio: float | None = None
    turbulence: str = "unknown"
    volume_grade: str | None = None
    one_min_agreement: str | None = None
    bar_minutes_elapsed: float | None = None
    # What satisfied the alignment filter: aligned, first-cross, other-index or none.
    confirmation: str = "none"
    prior_crosses: int | None = None
    # Minutes since the other symbol's most recent same-direction regular-hours cross before this one, if any.
    other_lead_min: float | None = None

    @property
    def traded(self) -> bool:
        return self.entry_price is not None and self.exit_price is not None


TIME_BUCKETS = (
    (dt_time(10, 0), "09:30-10:00"),
    (dt_time(11, 0), "10:00-11:00"),
    (dt_time(12, 0), "11:00-12:00"),
    (dt_time(13, 0), "12:00-13:00"),
    (dt_time(14, 0), "13:00-14:00"),
    (dt_time(15, 0), "14:00-15:00"),
)


def time_bucket(entry_et: datetime) -> str:
    clock = entry_et.time()
    for upper, name in TIME_BUCKETS:
        if clock < upper:
            return name
    return "15:00-close"


def lag_bucket(lag_min: float | None) -> str:
    if lag_min is None or pd.isna(lag_min):
        return "none"
    for upper, name in LAG_BUCKETS:
        if lag_min <= upper:
            return name
    return ">30"


def _parse_ts(value: object) -> datetime:
    return pd.Timestamp(value).tz_convert("UTC").to_pydatetime() if pd.Timestamp(value).tzinfo else pd.Timestamp(value, tz="UTC").to_pydatetime()


def _floor_seconds(value: datetime) -> datetime:
    return value.replace(microsecond=0)


def simulate_episode(
    episode: dict[str, object],
    evaluations: pd.DataFrame,
    option_bars: dict[str, list[OptionBar]],
    cfg: SimConfig,
    market_timezone: ZoneInfo,
    context: DayContext | None = None,
    session_crosses: dict[str, list[tuple[str, datetime]]] | None = None,
) -> list[TradeResult]:
    """Simulate one crossover episode for every strike label in the config.

    `session_crosses` maps each symbol to its regular-hours crossovers that day as (direction, cross_time); it feeds
    the first-cross and other-index confirmations and is optional.
    """
    direction = Direction(str(episode["direction"]))
    day_context = context or DayContext()
    symbol = str(episode["symbol"])
    day = date.fromisoformat(str(episode["date"]))
    cross_time = _floor_seconds(_parse_ts(episode["cross_time"]))
    detection_time = _parse_ts(episode["detection_time"])
    lag_value = _optional_float(episode.get("sma_cross_lag_min"))

    def base(label: str, reason: str) -> TradeResult:
        occ = str(episode.get(f"{label.lower()}_symbol", ""))
        return TradeResult(
            symbol=symbol,
            date=day.isoformat(),
            direction=direction.value,
            cross_time=cross_time.isoformat(),
            detection_time=detection_time.isoformat(),
            label=label,
            occ_symbol=occ,
            strike=_strike_from_occ(occ),
            target_pct=cfg.target_pct,
            stop_pct=cfg.stop_pct,
            exit_reason=reason,
            trail_trigger_pct=cfg.trail_trigger_pct,
            lag_min=lag_value,
            lag_bucket=lag_bucket(lag_value),
            bar_minutes_elapsed=_optional_float(episode.get("bar_minutes_elapsed")),
            vix_prev_close=day_context.vix_prev_close,
            vix_regime=day_context.vix_regime,
            trend_label=day_context.trend_label,
            alignment=alignment(direction, day_context.trend_bias) if day_context.trend_label != "unknown" else "unknown",
            opening_range_ratio=day_context.opening_range_ratio,
        )

    entry_row = _find_entry_row(evaluations, direction, cross_time, detection_time, cfg)
    if entry_row is None:
        return [base(label, "no_entry") for label in cfg.labels]

    entry_minute = _parse_ts(entry_row["timestamp"])
    fill_time = entry_minute + timedelta(minutes=1)
    entry_underlying = float(entry_row["last_price"])
    entry_gap = _signed_gap(entry_row, direction)
    flat_at = datetime.combine(day, dt_time.fromisoformat(cfg.flat_time), tzinfo=market_timezone)
    fill_et = fill_time.astimezone(market_timezone)
    after_cutoff = cfg.last_entry_time is not None and fill_et.time() > dt_time.fromisoformat(cfg.last_entry_time)
    entry_grade = str(entry_row["grade"])
    trade_alignment = alignment(direction, day_context.trend_bias) if day_context.trend_label != "unknown" else "unknown"
    turbulence = day_context.turbulence if fill_et.time() >= OPENING_RANGE_END else "pre_open_range"
    first_cross, other_confirmed, prior_crosses = _session_confirmation(session_crosses, symbol, direction, cross_time, fill_time, cfg)
    other_lead = _other_lead_minutes(session_crosses, symbol, direction, cross_time)
    confirmation = _confirmation(cfg, entry_grade, trade_alignment, first_cross, other_confirmed)
    below_min_grade = GRADE_RANK[Grade(entry_grade)] < GRADE_RANK[Grade(cfg.min_grade)]
    blocked = (
        ("filtered_grade" if below_min_grade else None)
        or _time_window_block(cfg, direction, fill_et)
        or _entry_filter_reason(cfg, entry_grade, lag_value, direction, confirmation, turbulence, other_lead)
    )

    results: list[TradeResult] = []
    for label in cfg.labels:
        result = base(label, "after_cutoff" if after_cutoff else (blocked or "no_fill"))
        result.confirmation = confirmation
        result.prior_crosses = prior_crosses
        result.other_lead_min = other_lead
        result.entry_grade = str(entry_row["grade"])
        result.entry_delay_min = round((fill_time - detection_time).total_seconds() / 60.0, 2)
        result.entry_hour_et = fill_et.hour
        result.entry_time_et = fill_et.strftime("%H:%M")
        result.time_bucket = time_bucket(fill_et)
        result.day_of_week = fill_et.strftime("%a")
        # The opening range is only known at 9:45; earlier fills cannot have used it.
        result.turbulence = turbulence
        result.volume_grade = str(entry_row["volume_grade"])
        result.one_min_agreement = str(entry_row["one_min_agreement"])
        if after_cutoff or blocked:
            results.append(result)
            continue
        bars = option_bars.get(result.occ_symbol, [])
        fill_index = next(
            (index for index, bar in enumerate(bars) if fill_time <= bar.timestamp <= fill_time + timedelta(minutes=FILL_SEARCH_MINUTES)),
            None,
        )
        if fill_index is None:
            results.append(result)
            continue
        _run_exit_path(
            result=result,
            bars=bars[fill_index:],
            evaluations=evaluations,
            direction=direction,
            entry_underlying=entry_underlying,
            entry_gap=entry_gap,
            flat_at=flat_at,
            cfg=cfg,
        )
        results.append(result)
    return results


def _confirmation(cfg: SimConfig, entry_grade: str, trade_alignment: str, first_cross: bool, other_confirmed: bool) -> str:
    """What satisfies the alignment filters: the day's bias, or for A+ an intraday confirmation; "none" otherwise."""
    if trade_alignment == "aligned":
        return "aligned"
    if cfg.a_plus_confirmation and entry_grade == Grade.A_PLUS.value:
        if first_cross:
            return "first-cross"
        if other_confirmed:
            return "other-index"
    return "none"


def _entry_filter_reason(
    cfg: SimConfig,
    entry_grade: str,
    lag_min: float | None,
    direction: Direction,
    confirmation: str,
    turbulence: str,
    other_lead_min: float | None = None,
) -> str | None:
    """Why an otherwise-alertable entry is declined by the optional filters, or None to trade it."""
    if cfg.plain_a_max_lag_min is not None and entry_grade != Grade.A_PLUS.value:
        if lag_min is None or lag_min > cfg.plain_a_max_lag_min:
            return "filtered_lag"
    if cfg.require_alignment and confirmation == "none":
        return "filtered_direction"
    if cfg.bull_requires_alignment and direction == Direction.BULL and confirmation == "none":
        return "filtered_direction"
    if cfg.skip_turbulent and turbulence == "turbulent":
        return "filtered_turbulence"
    if is_late_follower(other_lead_min, cfg.late_follower_min_minutes, cfg.late_follower_max_minutes):
        return "filtered_late_follower"
    return None


def _other_lead_minutes(
    session_crosses: dict[str, list[tuple[str, datetime]]] | None,
    symbol: str,
    direction: Direction,
    cross_time: datetime,
) -> float | None:
    """Minutes between this cross and the other symbols' most recent earlier same-direction regular-hours cross."""
    if session_crosses is None:
        return None
    earlier = [
        when
        for other_symbol, crosses in session_crosses.items()
        if other_symbol != symbol.upper()
        for side, when in crosses
        if side == direction.value and when < cross_time
    ]
    if not earlier:
        return None
    return round((cross_time - max(earlier)).total_seconds() / 60.0, 2)


def _session_confirmation(
    session_crosses: dict[str, list[tuple[str, datetime]]] | None,
    symbol: str,
    direction: Direction,
    cross_time: datetime,
    fill_time: datetime,
    cfg: SimConfig,
) -> tuple[bool, bool, int | None]:
    """(first cross of the day for this symbol, other symbol crossed the same way before the fill, prior cross count)."""
    if session_crosses is None:
        return False, False, None
    own = session_crosses.get(symbol.upper(), [])
    prior = sum(1 for _, when in own if when < cross_time)
    window = timedelta(minutes=cfg.confirmation_window_min)
    other = any(
        side == direction.value and fill_time - window <= when <= fill_time
        for other_symbol, crosses in session_crosses.items()
        if other_symbol != symbol.upper()
        for side, when in crosses
    )
    return prior == 0, other, prior


def _time_window_block(cfg: SimConfig, direction: Direction, fill_et: datetime) -> str | None:
    if cfg.first_entry_time is not None and fill_et.time() < dt_time.fromisoformat(cfg.first_entry_time):
        return "filtered_time"
    if cfg.bull_last_entry_time is not None and direction == Direction.BULL and fill_et.time() > dt_time.fromisoformat(cfg.bull_last_entry_time):
        return "filtered_time"
    return None


def _find_entry_row(
    evaluations: pd.DataFrame,
    direction: Direction,
    cross_time: datetime,
    detection_time: datetime,
    cfg: SimConfig,
) -> pd.Series | None:
    min_rank = GRADE_RANK[Grade(cfg.min_grade)]
    floor_rank = GRADE_RANK[Grade(cfg.entry_floor_grade)] if cfg.entry_floor_grade else None
    deadline = detection_time + timedelta(minutes=cfg.max_entry_delay_min)
    candidates = evaluations[
        (evaluations["direction"] == direction.value)
        & (evaluations["cross_time_s"] == cross_time)
        & (evaluations["timestamp"] >= detection_time)
        & (evaluations["timestamp"] <= deadline)
    ].sort_values("timestamp")
    for _, row in candidates.iterrows():
        rank = GRADE_RANK[Grade(str(row["grade"]))]
        if floor_rank is not None and rank >= floor_rank:
            return row  # the live engine decides here; simulate_episode declines it if the grade is below min_grade
        if rank >= min_rank:
            return row
    return None


def _run_exit_path(
    *,
    result: TradeResult,
    bars: list[OptionBar],
    evaluations: pd.DataFrame,
    direction: Direction,
    entry_underlying: float,
    entry_gap: float | None,
    flat_at: datetime,
    cfg: SimConfig,
) -> None:
    entry_bar = bars[0]
    fill_time = entry_bar.timestamp
    entry_price = round(entry_bar.open + cfg.slippage, 4)
    target_price = entry_price * (1 + cfg.target_pct / 100.0)
    stop_price = entry_price * (1 - cfg.stop_pct / 100.0)
    check_at = fill_time + timedelta(minutes=cfg.check_minutes)
    recheck_at = fill_time + timedelta(minutes=cfg.recheck_minutes)
    max_at = fill_time + timedelta(minutes=cfg.max_hold_minutes)
    checked = False
    rechecked = False
    highest = entry_bar.open
    lowest = entry_bar.open

    result.entry_time = fill_time.isoformat()
    result.entry_price = entry_price

    def close_out(bar: OptionBar, price: float, reason: str) -> None:
        exit_price = round(price, 4)
        result.exit_time = bar.timestamp.isoformat()
        result.exit_price = exit_price
        result.exit_reason = reason
        result.hold_minutes = round((bar.timestamp - fill_time).total_seconds() / 60.0, 2)
        result.pnl_usd = round((exit_price - entry_price) * 100.0, 2)
        result.return_pct = round((exit_price - entry_price) / entry_price * 100.0, 2)
        result.mfe_pct = round((highest - entry_price) / entry_price * 100.0, 2)
        result.mae_pct = round((lowest - entry_price) / entry_price * 100.0, 2)

    trail_armed = False
    last_bar: OptionBar | None = None
    for bar in bars:
        last_bar = bar
        # The trailing stop is set from the highs seen before this bar, so an intrabar high cannot
        # protect the same bar's low; that keeps the simulation on the conservative side.
        active_stop = stop_price
        trail_level = None
        if trail_armed:
            trail_level = highest * (1 - cfg.trail_pct / 100.0)
            active_stop = max(stop_price, trail_level)
        lowest = min(lowest, bar.low)
        # Intrabar: a resting stop and a resting limit; when both print in one bar, assume the stop went first.
        if bar.low <= active_stop:
            highest = max(highest, bar.high)
            reason = "trail_stop" if trail_level is not None and active_stop == trail_level else "stop"
            close_out(bar, active_stop - cfg.slippage, reason)
            return
        highest = max(highest, bar.high)
        if cfg.trail_trigger_pct is not None and highest >= entry_price * (1 + cfg.trail_trigger_pct / 100.0):
            trail_armed = True
        if bar.high >= target_price:
            close_out(bar, target_price, "target")
            return
        if bar.timestamp >= flat_at:
            close_out(bar, bar.close - cfg.slippage, "eod_flat")
            return
        if bar.timestamp >= max_at:
            close_out(bar, bar.close - cfg.slippage, "max_hold")
            return
        if cfg.time_rule_mode != "off":
            if not checked and bar.timestamp >= check_at:
                checked = True
                if _time_rule_fires(bar, entry_price, evaluations, direction, entry_underlying, entry_gap, cfg):
                    close_out(bar, bar.close - cfg.slippage, "time_rule")
                    return
            if checked and not rechecked and bar.timestamp >= recheck_at:
                rechecked = True
                if _time_rule_fires(bar, entry_price, evaluations, direction, entry_underlying, entry_gap, cfg):
                    close_out(bar, bar.close - cfg.slippage, "time_rule_recheck")
                    return

    if last_bar is not None:
        close_out(last_bar, last_bar.close - cfg.slippage, "no_more_bars")


def _time_rule_fires(
    bar: OptionBar,
    entry_price: float,
    evaluations: pd.DataFrame,
    direction: Direction,
    entry_underlying: float,
    entry_gap: float | None,
    cfg: SimConfig,
) -> bool:
    if cfg.time_rule_mode == "option" and bar.close > entry_price:
        return False
    return not _trend_holds(evaluations, bar.timestamp, direction, entry_underlying, entry_gap)


def _trend_holds(
    evaluations: pd.DataFrame,
    at: datetime,
    direction: Direction,
    entry_underlying: float,
    entry_gap: float | None,
) -> bool:
    """Two of three must hold: underlying beyond entry, 1m agreement, aligned 5m structure with a wider SMA gap."""
    rows = evaluations[(evaluations["direction"] == direction.value) & (evaluations["timestamp"] <= at)]
    if rows.empty:
        return False
    row = rows.iloc[-1]
    is_bull = direction == Direction.BULL
    price = float(row["last_price"])
    underlying_ok = price > entry_underlying if is_bull else price < entry_underlying
    one_min_ok = str(row["one_min_agreement"]) == "yes"
    aligned_label = "above" if is_bull else "below_or_equal"
    gap = _signed_gap(row, direction)
    structure_ok = (
        str(row["vwap_relation"]) == aligned_label
        and str(row["ema9_relation"]) == aligned_label
        and gap is not None
        and entry_gap is not None
        and gap >= entry_gap
    )
    return sum((underlying_ok, one_min_ok, structure_ok)) >= 2


def _signed_gap(row: pd.Series, direction: Direction) -> float | None:
    sma15 = _optional_float(row.get("sma15_value"))
    sma30 = _optional_float(row.get("sma30_value"))
    if sma15 is None or sma30 is None:
        return None
    gap = sma15 - sma30
    return gap if direction == Direction.BULL else -gap


def _optional_float(value: object) -> float | None:
    if value is None or value == "" or (isinstance(value, float) and pd.isna(value)):
        return None
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _strike_from_occ(occ_symbol: str) -> float | None:
    if len(occ_symbol) < 9 or not occ_symbol[-8:].isdigit():
        return None
    return int(occ_symbol[-8:]) / 1000.0


# ---------------------------------------------------------------------------
# Day loading and caching


def evaluations_path(data_dir: Path, symbol: str, day: date) -> Path:
    return data_dir / "evaluations" / symbol.upper() / f"{symbol.upper()}_evaluations_{day.isoformat()}.csv"


def build_evaluations_cache(config: AppConfig, data_dir: Path, symbol: str, day: date) -> Path | None:
    """Run the evaluator once per day and persist every row; later sweeps read the cache instead."""
    underlying = data_dir / "underlying" / symbol.upper() / f"{symbol.upper()}_1minute_{day.isoformat()}.csv"
    if not underlying.exists():
        return None
    path = evaluations_path(data_dir, symbol, day)
    if path.exists():
        return path
    candles = CsvReplayAdapter().load_candles(underlying, [symbol])
    evaluations, _, _ = evaluate_symbol(candles, config)
    records = [item.to_record() for item in evaluations]
    path.parent.mkdir(parents=True, exist_ok=True)
    frame = pd.DataFrame(records)
    frame.to_csv(path, index=False)
    return path


def load_evaluations(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path)
    frame["timestamp"] = pd.to_datetime(frame["datetime"], utc=True)
    cross = pd.to_datetime(frame["sma_cross_time"], utc=True, errors="coerce")
    frame["cross_time_s"] = cross.dt.floor("s")
    return frame


def load_option_bars(path: Path) -> dict[str, list[OptionBar]]:
    if not path.exists():
        return {}
    bars: dict[str, list[OptionBar]] = {}
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            bars.setdefault(row["occ_symbol"], []).append(
                OptionBar(
                    timestamp=_parse_ts(row["timestamp"]),
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                )
            )
    for series in bars.values():
        series.sort(key=lambda bar: bar.timestamp)
    return bars


def load_episodes(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def load_session_crosses(
    data_path: Path,
    symbols: list[str],
    day: date,
    market_timezone: ZoneInfo,
    market_open: dt_time,
) -> dict[str, list[tuple[str, datetime]]]:
    """Regular-hours crossovers for every symbol on one day, as the live engine would have seen them."""
    crosses: dict[str, list[tuple[str, datetime]]] = {}
    for symbol in symbols:
        rows = load_episodes(data_path / "episodes" / symbol.upper() / f"{symbol.upper()}_episodes_{day.isoformat()}.csv")
        session = []
        for row in rows:
            when = _floor_seconds(_parse_ts(row["cross_time"]))
            if when.astimezone(market_timezone).time() >= market_open:
                session.append((str(row["direction"]), when))
        crosses[symbol.upper()] = session
    return crosses


def run_simulation(
    config: AppConfig,
    data_dir: str | Path,
    symbols: list[str],
    start: date,
    end: date,
    grid: list[SimConfig],
    *,
    printer: Callable[[str], None] = print,
    cache_only: bool = False,
) -> pd.DataFrame:
    data_path = Path(data_dir)
    market_timezone = ZoneInfo(config.app.market_timezone)
    vix = load_vix_history(data_path / "context" / "VIX_History.csv")
    if vix is None:
        printer("No VIX history at data/context/VIX_History.csv (run fetch-vix); VIX regime will be 'unknown'.")
    results: list[TradeResult] = []
    market_open = dt_time.fromisoformat(config.live.market_open_time)
    crosses_by_day: dict[date, dict[str, list[tuple[str, datetime]]]] = {}
    for symbol in symbols:
        daily = build_daily_summary(data_path, symbol, market_timezone) if not cache_only else None
        day = start
        days_done = 0
        while day <= end:
            if day.weekday() < 5:
                cache = build_evaluations_cache(config, data_path, symbol, day)
                if cache is not None and not cache_only:
                    episodes = load_episodes(data_path / "episodes" / symbol.upper() / f"{symbol.upper()}_episodes_{day.isoformat()}.csv")
                    if episodes:
                        evaluations = load_evaluations(cache)
                        option_bars = load_option_bars(data_path / "options" / symbol.upper() / f"{symbol.upper()}_options_{day.isoformat()}.csv")
                        context = context_for_day(daily, vix, day) if daily is not None else None
                        if day not in crosses_by_day:
                            crosses_by_day[day] = load_session_crosses(data_path, symbols, day, market_timezone, market_open)
                        for cfg in grid:
                            for episode in episodes:
                                results.extend(
                                    simulate_episode(episode, evaluations, option_bars, cfg, market_timezone, context, session_crosses=crosses_by_day[day])
                                )
                if cache is not None:
                    days_done += 1
                    if days_done % 20 == 0:
                        printer(f"{symbol}: {days_done} days through {day}")
            day += timedelta(days=1)
        printer(f"{symbol}: {days_done} days processed")
    return pd.DataFrame([asdict(item) for item in results], columns=[field.name for field in fields(TradeResult)])


def summarize(trades: pd.DataFrame, by: list[str]) -> pd.DataFrame:
    """Per-bucket hit rates and expectancy over trades that actually filled."""
    if trades.empty:
        return pd.DataFrame()
    filled = trades[trades["entry_price"].notna() & trades["exit_price"].notna()].copy()
    if filled.empty:
        return pd.DataFrame()
    filled["win"] = filled["pnl_usd"] > 0
    filled["hit_target"] = filled["exit_reason"] == "target"
    filled["stopped"] = filled["exit_reason"] == "stop"

    def _agg(group: pd.DataFrame) -> pd.Series:
        wins = group.loc[group["pnl_usd"] > 0, "pnl_usd"].sum()
        losses = -group.loc[group["pnl_usd"] < 0, "pnl_usd"].sum()
        return pd.Series(
            {
                "trades": len(group),
                "win_rate": round(group["win"].mean(), 3),
                "target_rate": round(group["hit_target"].mean(), 3),
                "stop_rate": round(group["stopped"].mean(), 3),
                "avg_return_pct": round(group["return_pct"].mean(), 2),
                "expectancy_usd": round(group["pnl_usd"].mean(), 2),
                "total_pnl_usd": round(group["pnl_usd"].sum(), 2),
                "profit_factor": round(wins / losses, 2) if losses > 0 else float("inf"),
                "median_hold_min": round(group["hold_minutes"].median(), 1),
            }
        )

    return filled.groupby(by, dropna=False).apply(_agg, include_groups=False).reset_index()


MFE_LEVELS = (5, 10, 15, 20, 25, 30, 40, 50, 60)
POLICY_KEYS = ["target_pct", "stop_pct", "trail_trigger_pct", "label"]


def mfe_reach_table(trades: pd.DataFrame, by: list[str]) -> pd.DataFrame:
    """Share of filled trades whose option traded at least +N% above entry at some point before exit.

    This is what a fixed target can hope to capture, so it points at the target more directly than
    re-running the simulation for every candidate.
    """
    if trades.empty:
        return pd.DataFrame()
    filled = trades[trades["entry_price"].notna() & trades["exit_price"].notna()].copy()
    if filled.empty:
        return pd.DataFrame()
    for level in MFE_LEVELS:
        filled[f"reach_{level}"] = filled["mfe_pct"] >= level
    aggregated = filled.groupby(by, dropna=False)[[f"reach_{level}" for level in MFE_LEVELS]].mean().round(3)
    aggregated.insert(0, "trades", filled.groupby(by, dropna=False).size())
    return aggregated.reset_index()


def write_outputs(trades: pd.DataFrame, out_dir: str | Path, printer: Callable[[str], None] = print) -> None:
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    trades.to_csv(out_path / "trades.csv", index=False)
    if trades.empty:
        printer("No trades simulated.")
        return
    counts = trades.groupby([*POLICY_KEYS, "exit_reason"], dropna=False).size().reset_index(name="n")
    counts.to_csv(out_path / "exit_reasons.csv", index=False)
    for name, by in {
        "by_target": POLICY_KEYS,
        "by_grade": [*POLICY_KEYS, "entry_grade"],
        "by_lag": [*POLICY_KEYS, "lag_bucket"],
        "by_hour": [*POLICY_KEYS, "entry_hour_et"],
        "by_time_bucket": [*POLICY_KEYS, "time_bucket"],
        "by_weekday": [*POLICY_KEYS, "day_of_week"],
        "by_weekday_time": [*POLICY_KEYS, "day_of_week", "time_bucket"],
        "by_direction": [*POLICY_KEYS, "direction"],
        "by_symbol": [*POLICY_KEYS, "symbol"],
        "by_vix": [*POLICY_KEYS, "vix_regime"],
        "by_alignment": [*POLICY_KEYS, "alignment"],
        "by_vix_alignment": [*POLICY_KEYS, "vix_regime", "alignment"],
        "by_turbulence": [*POLICY_KEYS, "turbulence"],
        "by_trend_direction": [*POLICY_KEYS, "trend_label", "direction"],
    }.items():
        summary = summarize(trades, by)
        summary.to_csv(out_path / f"summary_{name}.csv", index=False)
    reach = mfe_reach_table(trades, ["stop_pct", "trail_trigger_pct", "label", "entry_grade"])
    reach.to_csv(out_path / "summary_mfe_reach.csv", index=False)
    funnel = trades["exit_reason"].value_counts()
    declined = {
        reason: int(funnel.get(reason, 0))
        for reason in ("no_entry", "filtered_grade", "after_cutoff", "filtered_time", "filtered_lag", "filtered_direction", "filtered_turbulence", "filtered_late_follower", "no_fill")
    }
    printer(f"Wrote {len(trades)} rows to {out_path / 'trades.csv'}; declined: {declined}")
    printer("== by policy ==")
    printer(summarize(trades, POLICY_KEYS).to_string(index=False))
    printer("== by entry grade ==")
    printer(summarize(trades, [*POLICY_KEYS, "entry_grade"]).to_string(index=False))
    printer("== MFE reach rates by grade (share of trades that touched +N%) ==")
    printer(reach.to_string(index=False))
