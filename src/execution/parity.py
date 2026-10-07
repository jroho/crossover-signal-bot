"""End-of-day data parity check: re-evaluate the session on full SIP bars and compare with what the live loop saw.

On the free data plan the live loop grades the last 15 minutes of every poll on IEX bars with scaled volume, while
the backtest graded consolidated SIP bars. This measures how often that gap changes a grade or an entry decision,
which is the number that decides whether the paid SIP/OPRA plan is worth its cost.
"""

from __future__ import annotations

import csv
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, time as dt_time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from src.backtest.context import DayContext, vix_regime
from src.config import AppConfig
from src.models import GRADE_RANK, Grade, SetupEvaluation

from .broker import AccountSnapshot, Contract, DryRunBroker, Quote
from .engine import ExecutionEngine
from .journal import TradeJournal

ROW_COLUMNS = ["symbol", "direction", "datetime", "grade", "volume_grade", "volume", "recent_volume_avg", "sma_cross_status", "bar_minutes_elapsed"]
# Skips that depend on exit state the parity replay does not model (flat quotes never hit a target or stop).
ARTIFACT_REASONS = ("halted", "max open positions", "no contract/quote")
ENTRY_PATTERN = re.compile(r"^ENTRY (?P<symbol>\S+) (?P<direction>bull|bear) (?P<grade>\S+) .*\((?P<reason>[^)]*)\)$")
SKIP_PATTERN = re.compile(r"^skip (?P<symbol>\S+) (?P<direction>bull|bear)(?: (?P<grade>A\+|A|B|C))?: (?P<reason>.*)$")
CONTEXT_PATTERN = re.compile(r"^(?P<symbol>\S+) context: bias=(?P<label>\w+) vix=(?P<regime>\w+) \((?P<vix>[^)]*)\)$")
BIAS_VALUES = {"bull": 1, "bear": -1, "neutral": 0, "unknown": 0}


@dataclass(frozen=True)
class Decision:
    kind: str  # ENTRY or skip
    symbol: str
    direction: str
    grade: str | None
    reason: str

    @property
    def artifact(self) -> bool:
        return self.kind == "skip" and any(marker in self.reason for marker in ARTIFACT_REASONS)

    def __str__(self) -> str:
        grade = f" {self.grade}" if self.grade else ""
        return f"{self.kind} {self.symbol} {self.direction}{grade}: {self.reason}"


@dataclass
class ParityResult:
    day: date
    minutes_compared: int = 0
    live_only: int = 0
    sip_only: int = 0
    grade_mismatches: pd.DataFrame = field(default_factory=pd.DataFrame)
    volume_grade_mismatches: int = 0
    decision_relevant_mismatches: int = 0
    live_decisions: list[Decision] = field(default_factory=list)
    sip_decisions: list[Decision] = field(default_factory=list)
    live_modes: list[str] = field(default_factory=list)
    sip_end: datetime | None = None

    @property
    def grade_mismatch_count(self) -> int:
        return len(self.grade_mismatches)

    @property
    def live_entries(self) -> list[Decision]:
        return [item for item in self.live_decisions if item.kind == "ENTRY"]

    @property
    def sip_entries(self) -> list[Decision]:
        return [item for item in self.sip_decisions if item.kind == "ENTRY"]

    def decision_diff(self) -> tuple[list[Decision], list[Decision]]:
        """(live-only, SIP-only) decisions, ignoring skips caused by exit state the replay does not model."""
        live = [item for item in self.live_decisions if not item.artifact]
        sip = [item for item in self.sip_decisions if not item.artifact]
        live_only = list(live)
        sip_only = list(sip)
        for item in live:
            if item in sip_only:
                sip_only.remove(item)
                live_only.remove(item)
        return live_only, sip_only

    @property
    def entries_match(self) -> bool:
        return sorted(map(str, self.live_entries)) == sorted(map(str, self.sip_entries))


# ----------------------------------------------------------------------------- live side


def day_bounds(day: date, config: AppConfig) -> tuple[datetime, datetime]:
    """Session start (config live.session_start_time) to midnight, as UTC datetimes."""
    market_timezone = ZoneInfo(config.app.market_timezone)
    start_clock = dt_time.fromisoformat(config.live.session_start_time)
    start = datetime.combine(day, start_clock, tzinfo=market_timezone)
    end = datetime.combine(day + timedelta(days=1), dt_time(0, 0), tzinfo=market_timezone)
    return start.astimezone(ZoneInfo("UTC")), end.astimezone(ZoneInfo("UTC"))


def load_live_rows(sqlite_path: str | Path, day: date, config: AppConfig) -> tuple[pd.DataFrame, list[str]]:
    """The evaluation of every (symbol, direction, minute) as the trade loop first saw it that day.

    A restarted loop re-logs earlier minutes from SIP data; the earliest row per key is the one the engine acted on.
    """
    start, end = day_bounds(day, config)
    with sqlite3.connect(sqlite_path) as connection:
        frame = pd.read_sql_query(
            "SELECT e.id, e.symbol, e.direction, e.datetime, e.grade, e.volume_grade, e.volume, e.recent_volume_avg, "
            "e.sma_cross_status, e.bar_minutes_elapsed, r.mode FROM evaluated_setups e JOIN runs r ON r.run_id = e.run_id "
            "WHERE r.mode LIKE 'trade-%' AND substr(e.datetime, 1, 10) IN (?, ?) ORDER BY e.id",
            connection,
            params=(day.isoformat(), (day + timedelta(days=1)).isoformat()),
        )
    if frame.empty:
        return pd.DataFrame(columns=ROW_COLUMNS), []
    frame["datetime"] = pd.to_datetime(frame["datetime"], utc=True)
    frame = frame[(frame["datetime"] >= start) & (frame["datetime"] < end)]
    modes = sorted(frame["mode"].unique().tolist())
    frame = frame.drop_duplicates(subset=["symbol", "direction", "datetime"], keep="first")
    return frame[ROW_COLUMNS].reset_index(drop=True), modes


def load_live_events(journal_sqlite_path: str | Path, day: date, config: AppConfig) -> list[str]:
    start, end = day_bounds(day, config)
    path = Path(journal_sqlite_path)
    if not path.exists():
        return []
    with sqlite3.connect(path) as connection:
        rows = connection.execute("SELECT at, message FROM live_events WHERE at >= ? AND at < ? ORDER BY id", (start.isoformat(), end.isoformat())).fetchall()
    return [str(message) for _, message in rows]


def parse_decision(message: str) -> Decision | None:
    match = ENTRY_PATTERN.match(message)
    if match:
        return Decision("ENTRY", match["symbol"], match["direction"], match["grade"], match["reason"])
    match = SKIP_PATTERN.match(message)
    if match:
        return Decision("skip", match["symbol"], match["direction"], match["grade"], match["reason"])
    return None


def parse_decisions(messages: list[str]) -> list[Decision]:
    return [item for item in (parse_decision(message) for message in messages) if item is not None]


def parse_contexts(messages: list[str]) -> dict[str, DayContext]:
    """The bias each symbol actually ran with (including any override), from the engine's own context lines."""
    contexts: dict[str, DayContext] = {}
    for message in messages:
        match = CONTEXT_PATTERN.match(message)
        if not match:
            continue
        label = match["label"]
        vix_text = match["vix"]
        vix_prev = None if vix_text in {"None", ""} else float(vix_text)
        contexts[match["symbol"].upper()] = DayContext(
            vix_prev_close=vix_prev,
            vix_regime=match["regime"] if match["regime"] != "unknown" else vix_regime(vix_prev),
            trend_bias=BIAS_VALUES.get(label, 0),
            trend_label=label,
        )
    return contexts


# ----------------------------------------------------------------------------- SIP side


def rows_from_evaluations(evaluations: list[SetupEvaluation]) -> pd.DataFrame:
    records = [
        {
            "symbol": item.symbol,
            "direction": item.direction.value,
            "datetime": item.timestamp,
            "grade": item.grade.value,
            "volume_grade": item.volume_grade,
            "volume": item.volume,
            "recent_volume_avg": item.recent_volume_avg,
            "sma_cross_status": item.sma_cross_status,
            "bar_minutes_elapsed": item.bar_minutes_elapsed,
        }
        for item in evaluations
    ]
    frame = pd.DataFrame(records, columns=ROW_COLUMNS)
    if not frame.empty:
        frame["datetime"] = pd.to_datetime(frame["datetime"], utc=True)
    return frame


def compare_rows(live: pd.DataFrame, sip: pd.DataFrame) -> tuple[pd.DataFrame, int, int, int, int]:
    """(grade mismatches, volume-grade-only mismatches, decision-relevant mismatches, live-only, sip-only)."""
    keys = ["symbol", "direction", "datetime"]
    merged = live.merge(sip, on=keys, how="outer", suffixes=("_live", "_sip"), indicator=True)
    live_only = int((merged["_merge"] == "left_only").sum())
    sip_only = int((merged["_merge"] == "right_only").sum())
    both = merged[merged["_merge"] == "both"].copy()
    grade_diff = both[both["grade_live"] != both["grade_sip"]]
    volume_only = both[(both["grade_live"] == both["grade_sip"]) & (both["volume_grade_live"] != both["volume_grade_sip"])]
    floor = GRADE_RANK[Grade.B]
    relevant = grade_diff[
        grade_diff["grade_live"].map(lambda g: GRADE_RANK[Grade(g)] >= floor) | grade_diff["grade_sip"].map(lambda g: GRADE_RANK[Grade(g)] >= floor)
    ]
    columns = keys + ["grade_live", "grade_sip", "volume_grade_live", "volume_grade_sip", "volume_live", "volume_sip", "recent_volume_avg_live", "recent_volume_avg_sip", "sma_cross_status_live", "sma_cross_status_sip"]
    return grade_diff[columns].sort_values(keys).reset_index(drop=True), len(volume_only), len(relevant), live_only, sip_only


@dataclass
class FlatQuoteSource:
    """Stands in for the real broker: every contract exists and quotes a flat premium, so entries can be decided."""

    bid: float = 1.48
    ask: float = 1.50
    equity: float = 100_000.0

    def account_equity(self) -> float:
        return self.equity

    def buying_power(self) -> float:
        return self.equity

    def account_snapshot(self) -> AccountSnapshot:
        return AccountSnapshot("ACTIVE", self.equity, self.equity, 3, 1, None)

    def is_market_open(self) -> bool:
        return True

    def find_contract(self, underlying: str, expiration: date, option_type: str, strike: float) -> Contract | None:
        from src.backtest.strikes import occ_symbol

        return Contract(occ_symbol(underlying, expiration, option_type, strike), underlying, expiration, option_type, strike)

    def latest_quote(self, occ_symbol: str) -> Quote | None:
        return Quote(occ_symbol, self.bid, self.ask, datetime.now(tz=ZoneInfo("UTC")))

    def submit_limit(self, *args, **kwargs):  # pragma: no cover - DryRunBroker fills; never called
        raise NotImplementedError

    def get_order(self, order_id: str):  # pragma: no cover
        raise NotImplementedError

    def cancel_order(self, order_id: str) -> None:  # pragma: no cover
        raise NotImplementedError

    def close_position(self, occ_symbol: str):  # pragma: no cover
        raise NotImplementedError

    def list_positions(self) -> list:
        return []


def replay_decisions(
    config: AppConfig,
    evaluations: list[SetupEvaluation],
    contexts: dict[str, DayContext],
    day: date,
    journal_dir: Path,
) -> list[str]:
    """Feed the SIP evaluations minute by minute through a fresh ExecutionEngine, exactly as the live loop does,
    and return every line it printed. Fills are simulated at a flat quote, so only entry decisions are meaningful."""
    journal_dir.mkdir(parents=True, exist_ok=True)
    journal = TradeJournal(journal_dir / "parity_journal.sqlite3", journal_dir / "parity_journal.csv")
    broker = DryRunBroker(FlatQuoteSource(), equity=config.trading.risk_capital_usd)
    lines: list[str] = []
    engine = ExecutionEngine(config, broker, journal, mode="parity", printer=lines.append)
    start, _ = day_bounds(day, config)
    engine.new_session(start)
    for symbol in config.app.symbols:
        engine.set_context(symbol, contexts.get(symbol.upper(), DayContext()), start)
    by_minute: dict[datetime, list[SetupEvaluation]] = {}
    for item in evaluations:
        by_minute.setdefault(item.timestamp, []).append(item)
    for minute in sorted(by_minute):
        batch = by_minute[minute]
        now = minute + timedelta(seconds=65)  # the live loop sees a bar a few seconds after it closes
        engine.record_crosses(batch)
        engine.on_evaluations(batch, now)
        engine.on_tick(now)
    return lines


# ----------------------------------------------------------------------------- report


def write_report(result: ParityResult, out_dir: Path, market_timezone: ZoneInfo) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    live_only, sip_only = result.decision_diff()
    lines = [
        f"# Data parity check {result.day.isoformat()}",
        "",
        f"Live modes: {', '.join(result.live_modes) or 'none'}. SIP bars through {result.sip_end.astimezone(market_timezone):%H:%M} ET." if result.sip_end else f"Live modes: {', '.join(result.live_modes) or 'none'}.",
        "",
        "| Measure | Count |",
        "|---|---|",
        f"| Minutes compared (symbol x direction) | {result.minutes_compared} |",
        f"| Grade mismatches | {result.grade_mismatch_count} |",
        f"| ...of which decision-relevant (B or better on either side) | {result.decision_relevant_mismatches} |",
        f"| Volume-grade-only mismatches | {result.volume_grade_mismatches} |",
        f"| Live minutes without SIP bars yet | {result.live_only} |",
        f"| SIP minutes the loop never evaluated | {result.sip_only} |",
        f"| Live entries | {len(result.live_entries)} |",
        f"| SIP-replay entries | {len(result.sip_entries)} |",
        f"| Entries match | {'yes' if result.entries_match else 'NO'} |",
        "",
        "## Entry decisions",
        "",
        "Live:",
        *([f"- {item}" for item in result.live_decisions] or ["- none"]),
        "",
        "SIP replay:",
        *([f"- {item}" for item in result.sip_decisions] or ["- none"]),
        "",
        "Only live: " + ("; ".join(map(str, live_only)) if live_only else "none"),
        "",
        "Only SIP replay: " + ("; ".join(map(str, sip_only)) if sip_only else "none"),
        "",
        "## Grade mismatches",
        "",
    ]
    if result.grade_mismatches.empty:
        lines.append("none")
    else:
        lines.append("| time ET | symbol | dir | live | SIP | vol live | vol SIP | volume live | volume SIP | avg live | avg SIP |")
        lines.append("|---|---|---|---|---|---|---|---|---|---|---|")
        for _, row in result.grade_mismatches.iterrows():
            stamp = row["datetime"].tz_convert(market_timezone).strftime("%H:%M")
            lines.append(
                f"| {stamp} | {row['symbol']} | {row['direction']} | {row['grade_live']} | {row['grade_sip']} | {row['volume_grade_live']} | {row['volume_grade_sip']} "
                f"| {row['volume_live']:.0f} | {row['volume_sip']:.0f} | {row['recent_volume_avg_live']:.0f} | {row['recent_volume_avg_sip']:.0f} |"
            )
    path = out_dir / f"parity_{result.day.isoformat()}.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def append_log(result: ParityResult, out_dir: Path) -> Path:
    """One row per day so the mismatch rate accumulates across the paper-trading period."""
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "parity_log.csv"
    live_only, sip_only = result.decision_diff()
    row = {
        "date": result.day.isoformat(),
        "live_modes": "|".join(result.live_modes),
        "minutes_compared": result.minutes_compared,
        "grade_mismatches": result.grade_mismatch_count,
        "decision_relevant_mismatches": result.decision_relevant_mismatches,
        "volume_grade_mismatches": result.volume_grade_mismatches,
        "live_entries": len(result.live_entries),
        "sip_entries": len(result.sip_entries),
        "entries_match": result.entries_match,
        "decisions_only_live": len(live_only),
        "decisions_only_sip": len(sip_only),
    }
    rows = []
    if path.exists():
        with path.open(newline="", encoding="utf-8") as handle:
            rows = [item for item in csv.DictReader(handle) if item.get("date") != row["date"]]
    rows.append({key: str(value) for key, value in row.items()})
    rows.sort(key=lambda item: item["date"])
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(row.keys()))
        writer.writeheader()
        writer.writerows(rows)
    return path


def summary_line(result: ParityResult) -> str:
    live_only, sip_only = result.decision_diff()
    return (
        f"{result.day.isoformat()}: {result.minutes_compared} minutes compared, {result.grade_mismatch_count} grade mismatches "
        f"({result.decision_relevant_mismatches} decision-relevant), {result.volume_grade_mismatches} volume-grade-only; "
        f"entries live {len(result.live_entries)} vs SIP {len(result.sip_entries)} ({'match' if result.entries_match else 'DIFFER'}); "
        f"decisions only-live {len(live_only)}, only-SIP {len(sip_only)}"
    )
