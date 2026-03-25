from __future__ import annotations

import argparse
import sqlite3
import time
from datetime import UTC, datetime, timedelta

from src.alerts import TelegramAlerter, format_alert
from src.backtest import ReplayEngine
from src.config import AppConfig, load_config
from src.data import PolygonAdapter
from src.models import AlertRecord, Direction, Grade, SetupEvaluation, StrikeBias, Timeframe
from src.signals import evaluate_symbol
from src.storage import SQLiteLogger, export_evaluations_to_csv


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Intraday indicator alert bot")
    parser.add_argument("--config", default="docs/config.example.toml", help="Path to TOML config file")
    subparsers = parser.add_subparsers(dest="command", required=True)

    replay = subparsers.add_parser("replay", help="Run replay mode from local CSV")
    replay.add_argument("--csv", default="", help="Replay CSV path")
    replay.add_argument("--export", default="", help="Optional evaluation CSV export path")

    live = subparsers.add_parser("live", help="Run minimal live polling with Polygon")
    live.add_argument("--poll-seconds", type=int, default=60, help="Polling interval in seconds")

    export_csv = subparsers.add_parser("export-csv", help="Export evaluations from the last run path")
    export_csv.add_argument("--output", required=True, help="Target CSV path")

    subparsers.add_parser("init-db", help="Initialize SQLite schema")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    config = load_config(args.config)
    logger = SQLiteLogger(config.storage.sqlite_path)

    if args.command == "init-db":
        logger.initialize()
        print(f"Initialized SQLite schema at {config.storage.sqlite_path}")
        return

    if args.command == "replay":
        engine = ReplayEngine(config=config, logger=logger)
        result = engine.run(csv_path=args.csv or None, export_path=args.export or None)
        print(f"Replay complete: {len(result.evaluations)} evaluations, {len(result.alerts)} alerts, run_id={result.run_id}")
        return

    if args.command == "export-csv":
        export_evaluations_to_csv(_read_evaluations_from_db(config.storage.sqlite_path), args.output)
        print(f"Exported evaluations to {args.output}")
        return

    if args.command == "live":
        _run_live_mode(config=config, logger=logger, poll_seconds=args.poll_seconds)
        return

    parser.error(f"Unknown command: {args.command}")


def _run_live_mode(config: AppConfig, logger: SQLiteLogger, poll_seconds: int) -> None:
    if not config.polygon.api_key:
        raise SystemExit("Polygon API key is required for live mode.")

    logger.initialize()
    adapter = PolygonAdapter(config)
    alerter = TelegramAlerter(config)
    run_id = logger.create_run(mode="live", config=config, source="polygon")
    seen_keys: set[tuple[str, str, str]] = set()

    while True:
        all_evaluations: list[SetupEvaluation] = []
        all_alerts: list[AlertRecord] = []
        for symbol in config.app.symbols:
            end = datetime.now(tz=UTC)
            start = end - timedelta(minutes=config.live.lookback_minutes)
            candles = adapter.get_historical_candles(symbol, timeframe=Timeframe.ONE_MINUTE, start=start, end=end)
            evaluations, _, _ = evaluate_symbol(candles, config)
            if not evaluations:
                continue
            latest_timestamp = max(item.timestamp for item in evaluations)
            latest_evaluations = [item for item in evaluations if item.timestamp == latest_timestamp]
            for evaluation in latest_evaluations:
                dedupe_key = (evaluation.symbol, evaluation.timestamp.isoformat(), evaluation.direction.value)
                if dedupe_key in seen_keys:
                    continue
                seen_keys.add(dedupe_key)
                if evaluation.grade.value in set(config.grading.alert_grades) and evaluation.strike_bias.value != "skip":
                    payload = format_alert(evaluation)
                    delivered, transport_message = alerter.send(payload)
                    evaluation.alert_sent = delivered
                    all_alerts.append(
                        AlertRecord(
                            evaluation=evaluation,
                            payload=payload,
                            delivered=delivered,
                            transport_message=transport_message,
                        )
                    )
                all_evaluations.append(evaluation)

        if all_evaluations:
            logger.log_evaluations(run_id, all_evaluations)
        if all_alerts:
            logger.log_alerts(run_id, all_alerts)
        time.sleep(max(5, poll_seconds))


def _read_evaluations_from_db(path: str) -> list[SetupEvaluation]:
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            "SELECT * FROM evaluated_setups ORDER BY datetime, symbol, direction"
        ).fetchall()

    evaluations: list[SetupEvaluation] = []
    for row in rows:
        evaluations.append(
            SetupEvaluation(
                symbol=row["symbol"],
                timestamp=datetime.fromisoformat(row["datetime"]),
                timeframe=Timeframe(row["timeframe"]),
                direction=Direction(row["direction"]),
                last_price=row["last_price"],
                vwap_relation=row["vwap_relation"],
                ema9_relation=row["ema9_relation"],
                sma15_value=row["sma15_value"],
                sma30_value=row["sma30_value"],
                sma_trend_relation=row["sma_trend_relation"],
                rvgi=row["rvgi"],
                rvgi_sma=row["rvgi_sma"],
                rvgi_vs_sma=row["rvgi_vs_sma"],
                rvgi_sign=row["rvgi_sign"],
                volume=row["volume"],
                recent_volume_avg=row["recent_volume_avg"],
                rolling_volume_avg=row["rolling_volume_avg"],
                volume_grade=row["volume_grade"],
                one_min_agreement=row["one_min_agreement"],
                grade=Grade(row["grade"]),
                strike_bias=StrikeBias(row["strike_bias"]),
                strike_bias_reason=row["strike_bias_reason"],
                passed_conditions=(row["passed_conditions"] or "").split("|") if row["passed_conditions"] else [],
                weak_conditions=(row["weak_conditions"] or "").split("|") if row["weak_conditions"] else [],
                failed_conditions=(row["failed_conditions"] or "").split("|") if row["failed_conditions"] else [],
                rationale=row["rationale"],
                alert_sent=bool(row["alert_sent"]),
                forward_return_3m=row["forward_return_3m"],
                forward_return_5m=row["forward_return_5m"],
                forward_return_10m=row["forward_return_10m"],
                forward_return_15m=row["forward_return_15m"],
            )
        )
    return evaluations


if __name__ == "__main__":
    main()
