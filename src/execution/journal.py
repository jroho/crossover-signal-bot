from __future__ import annotations

import csv
import sqlite3
from dataclasses import asdict, dataclass, fields
from datetime import datetime
from pathlib import Path


@dataclass
class LiveTrade:
    trade_id: str
    mode: str
    symbol: str
    direction: str
    grade: str
    cross_time: str
    occ_symbol: str
    strike: float
    contracts: int
    status: str = "pending_entry"
    opened_at: str | None = None
    entry_order_id: str | None = None
    entry_limit: float | None = None
    entry_price: float | None = None
    filled_at: str | None = None
    target_order_id: str | None = None
    target_price: float | None = None
    stop_price: float | None = None
    exit_order_id: str | None = None
    exit_price: float | None = None
    exit_reason: str | None = None
    closed_at: str | None = None
    pnl_usd: float | None = None
    decision: str = ""
    trend_label: str = "unknown"
    vix_regime: str = "unknown"
    lag_min: float | None = None
    notes: str = ""

    @property
    def is_open(self) -> bool:
        return self.status in {"pending_entry", "open"}


LIVE_TRADE_FIELDS = [item.name for item in fields(LiveTrade)]


class TradeJournal:
    """Every live decision and fill goes to SQLite and CSV; the journal is the source of truth for review."""

    def __init__(self, sqlite_path: str | Path, csv_path: str | Path) -> None:
        self.sqlite_path = Path(sqlite_path)
        self.csv_path = Path(csv_path)
        self.sqlite_path.parent.mkdir(parents=True, exist_ok=True)
        self.csv_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.sqlite_path) as connection:
            columns = ", ".join(f"{name} TEXT" for name in LIVE_TRADE_FIELDS if name != "trade_id")
            connection.execute(f"CREATE TABLE IF NOT EXISTS live_trades (trade_id TEXT PRIMARY KEY, {columns})")
            connection.execute(
                "CREATE TABLE IF NOT EXISTS live_events (id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, level TEXT NOT NULL, message TEXT NOT NULL)"
            )

    def upsert(self, trade: LiveTrade) -> None:
        record = asdict(trade)
        placeholders = ", ".join("?" for _ in LIVE_TRADE_FIELDS)
        with sqlite3.connect(self.sqlite_path) as connection:
            connection.execute(
                f"INSERT OR REPLACE INTO live_trades ({', '.join(LIVE_TRADE_FIELDS)}) VALUES ({placeholders})",
                [record[name] for name in LIVE_TRADE_FIELDS],
            )
        self._rewrite_csv()

    def event(self, at: datetime, level: str, message: str) -> None:
        with sqlite3.connect(self.sqlite_path) as connection:
            connection.execute("INSERT INTO live_events (at, level, message) VALUES (?, ?, ?)", (at.isoformat(), level, message))

    def load_open_trades(self) -> list[LiveTrade]:
        with sqlite3.connect(self.sqlite_path) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute("SELECT * FROM live_trades WHERE status IN ('pending_entry', 'open')").fetchall()
        return [_trade_from_row(row) for row in rows]

    def all_trades(self) -> list[LiveTrade]:
        with sqlite3.connect(self.sqlite_path) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute("SELECT * FROM live_trades ORDER BY opened_at").fetchall()
        return [_trade_from_row(row) for row in rows]

    def _rewrite_csv(self) -> None:
        trades = self.all_trades()
        with self.csv_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=LIVE_TRADE_FIELDS)
            writer.writeheader()
            for trade in trades:
                writer.writerow(asdict(trade))


def _trade_from_row(row: sqlite3.Row) -> LiveTrade:
    values = {name: row[name] for name in LIVE_TRADE_FIELDS}
    for name in ("strike", "entry_limit", "entry_price", "target_price", "stop_price", "exit_price", "pnl_usd", "lag_min"):
        values[name] = float(values[name]) if values[name] not in (None, "") else None
    values["contracts"] = int(values["contracts"]) if values["contracts"] not in (None, "") else 0
    return LiveTrade(**values)
