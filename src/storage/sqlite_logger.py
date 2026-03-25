from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime
from pathlib import Path

from src.config import AppConfig
from src.models import AlertRecord, SetupEvaluation


class SQLiteLogger:
    def __init__(self, path: str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def initialize(self) -> None:
        with sqlite3.connect(self.path) as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    mode TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    config_hash TEXT NOT NULL,
                    source TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS evaluated_setups (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    datetime TEXT NOT NULL,
                    timeframe TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    last_price REAL NOT NULL,
                    vwap_relation TEXT,
                    ema9_relation TEXT,
                    sma15_value REAL,
                    sma30_value REAL,
                    sma_trend_relation TEXT,
                    rvgi REAL,
                    rvgi_sma REAL,
                    rvgi_vs_sma TEXT,
                    rvgi_sign TEXT,
                    volume REAL,
                    recent_volume_avg REAL,
                    rolling_volume_avg REAL,
                    volume_grade TEXT,
                    one_min_agreement TEXT,
                    grade TEXT,
                    strike_bias TEXT,
                    strike_bias_reason TEXT,
                    passed_conditions TEXT,
                    weak_conditions TEXT,
                    failed_conditions TEXT,
                    rationale TEXT,
                    alert_sent INTEGER,
                    forward_return_3m REAL,
                    forward_return_5m REAL,
                    forward_return_10m REAL,
                    forward_return_15m REAL
                );

                CREATE TABLE IF NOT EXISTS alerts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    datetime TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    grade TEXT NOT NULL,
                    strike_bias TEXT NOT NULL,
                    delivered INTEGER NOT NULL,
                    transport_message TEXT NOT NULL,
                    title TEXT NOT NULL,
                    message TEXT NOT NULL
                );
                """
            )

    def create_run(self, *, mode: str, config: AppConfig, source: str) -> str:
        run_id = datetime.now(tz=UTC).strftime("%Y%m%d%H%M%S%f")
        config_hash = hashlib.sha256(json.dumps(_json_safe(config), sort_keys=True).encode("utf-8")).hexdigest()
        with sqlite3.connect(self.path) as connection:
            connection.execute(
                "INSERT INTO runs (run_id, mode, started_at, config_hash, source) VALUES (?, ?, ?, ?, ?)",
                (run_id, mode, datetime.now(tz=UTC).isoformat(), config_hash, source),
            )
        return run_id

    def log_evaluations(self, run_id: str, evaluations: list[SetupEvaluation]) -> None:
        rows = []
        for evaluation in evaluations:
            record = evaluation.to_record()
            rows.append(
                (
                    run_id,
                    record["symbol"],
                    record["datetime"],
                    record["timeframe"],
                    record["direction"],
                    record["last_price"],
                    record["vwap_relation"],
                    record["ema9_relation"],
                    record["sma15_value"],
                    record["sma30_value"],
                    record["sma_trend_relation"],
                    record["rvgi"],
                    record["rvgi_sma"],
                    record["rvgi_vs_sma"],
                    record["rvgi_sign"],
                    record["volume"],
                    record["recent_volume_avg"],
                    record["rolling_volume_avg"],
                    record["volume_grade"],
                    record["one_min_agreement"],
                    record["grade"],
                    record["strike_bias"],
                    record["strike_bias_reason"],
                    record["passed_conditions"],
                    record["weak_conditions"],
                    record["failed_conditions"],
                    record["rationale"],
                    record["alert_sent"],
                    record["forward_return_3m"],
                    record["forward_return_5m"],
                    record["forward_return_10m"],
                    record["forward_return_15m"],
                )
            )
        with sqlite3.connect(self.path) as connection:
            connection.executemany(
                """
                INSERT INTO evaluated_setups (
                    run_id, symbol, datetime, timeframe, direction, last_price,
                    vwap_relation, ema9_relation, sma15_value, sma30_value, sma_trend_relation,
                    rvgi, rvgi_sma, rvgi_vs_sma, rvgi_sign, volume, recent_volume_avg,
                    rolling_volume_avg, volume_grade, one_min_agreement, grade, strike_bias,
                    strike_bias_reason, passed_conditions, weak_conditions, failed_conditions,
                    rationale, alert_sent, forward_return_3m, forward_return_5m,
                    forward_return_10m, forward_return_15m
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )

    def log_alerts(self, run_id: str, alerts: list[AlertRecord]) -> None:
        rows = []
        for alert in alerts:
            rows.append(
                (
                    run_id,
                    alert.evaluation.symbol,
                    alert.evaluation.timestamp.isoformat(),
                    alert.evaluation.direction.value,
                    alert.evaluation.grade.value,
                    alert.evaluation.strike_bias.value,
                    int(alert.delivered),
                    alert.transport_message,
                    alert.payload.title,
                    alert.payload.message,
                )
            )
        with sqlite3.connect(self.path) as connection:
            connection.executemany(
                """
                INSERT INTO alerts (
                    run_id, symbol, datetime, direction, grade, strike_bias,
                    delivered, transport_message, title, message
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )


def _json_safe(value: object) -> object:
    if is_dataclass(value):
        return {key: _json_safe(inner) for key, inner in asdict(value).items()}
    if isinstance(value, dict):
        return {key: _json_safe(inner) for key, inner in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    return value
