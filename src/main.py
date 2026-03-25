from __future__ import annotations

import argparse
import sqlite3
import time
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from src.alerts import TelegramAlerter, format_alert
from src.backtest import ReplayEngine
from src.config import AppConfig, load_config
from src.data import PolygonAdapter
from src.market_hours import is_within_market_hours, parse_clock_time
from src.models import AlertRecord, Direction, Grade, SetupEvaluation, StrikeBias, Timeframe
from src.signals import evaluate_symbol
from src.storage import SQLiteLogger, export_evaluations_to_csv, export_polygon_aggregate_rows


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Intraday indicator alert bot")
    parser.add_argument("--config", default="docs/config.example.toml", help="Path to TOML config file")
    subparsers = parser.add_subparsers(dest="command", required=True)

    replay = subparsers.add_parser("replay", help="Run replay mode from local CSV")
    replay.add_argument("--config", default=argparse.SUPPRESS, help="Path to TOML config file")
    replay.add_argument("--csv", default="", help="Replay CSV path")
    replay.add_argument("--export", default="", help="Optional evaluation CSV export path")
    replay.add_argument(
        "--market",
        "--market-hours-only",
        dest="market_hours_only",
        action="store_true",
        help="Restrict replay to market hours in the configured market timezone",
    )

    live = subparsers.add_parser("live", help="Run minimal live polling with Polygon")
    live.add_argument("--config", default=argparse.SUPPRESS, help="Path to TOML config file")
    live.add_argument("--poll-seconds", type=int, default=60, help="Polling interval in seconds")
    live.add_argument(
        "--market",
        "--market-hours-only",
        dest="market_hours_only",
        action="store_true",
        help="Restrict live polling to market hours in the configured market timezone",
    )

    fetch_day = subparsers.add_parser("fetch-day", help="Fetch one day of Polygon minute aggregates to CSV")
    fetch_day.add_argument("--config", default=argparse.SUPPRESS, help="Path to TOML config file")
    fetch_day.add_argument("-date", "--date", dest="day", required=True, help="Trading day in YYYY-MM-DD format")
    fetch_day.add_argument(
        "-multiplier",
        "--multiplier",
        type=int,
        default=1,
        help="Minute aggregate multiplier",
    )
    fetch_day.add_argument("--symbol", default="", help="Ticker symbol; defaults to the first configured symbol")
    fetch_day.add_argument("--output", default="", help="Optional CSV output path")

    export_csv = subparsers.add_parser("export-csv", help="Export evaluations from the last run path")
    export_csv.add_argument("--config", default=argparse.SUPPRESS, help="Path to TOML config file")
    export_csv.add_argument("--output", required=True, help="Target CSV path")

    init_db = subparsers.add_parser("init-db", help="Initialize SQLite schema")
    init_db.add_argument("--config", default=argparse.SUPPRESS, help="Path to TOML config file")
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    config = load_config(args.config)
    logger = SQLiteLogger(config.storage.sqlite_path)

    if args.command == "init-db":
        logger.initialize()
        print(f"Initialized SQLite schema at {config.storage.sqlite_path}")
        return

    if args.command == "replay":
        engine = ReplayEngine(config=config, logger=logger)
        result = engine.run(
            csv_path=args.csv or None,
            export_path=args.export or None,
            market_hours_only=args.market_hours_only,
        )
        print(f"Replay complete: {len(result.evaluations)} evaluations, {len(result.alerts)} alerts, run_id={result.run_id}")
        return

    if args.command == "fetch-day":
        _run_fetch_day_command(
            config=config,
            symbol=args.symbol,
            day_text=args.day,
            multiplier=args.multiplier,
            output=args.output,
        )
        return

    if args.command == "export-csv":
        export_evaluations_to_csv(_read_evaluations_from_db(config.storage.sqlite_path), args.output)
        print(f"Exported evaluations to {args.output}")
        return

    if args.command == "live":
        _run_live_mode(
            config=config,
            logger=logger,
            poll_seconds=args.poll_seconds,
            market_hours_only=args.market_hours_only,
        )
        return

    parser.error(f"Unknown command: {args.command}")


def _run_fetch_day_command(
    config: AppConfig,
    symbol: str,
    day_text: str,
    multiplier: int,
    output: str,
) -> None:
    if not config.polygon.api_key:
        raise SystemExit("Polygon API key is required for fetch-day.")
    if multiplier < 1:
        raise SystemExit("Multiplier must be greater than or equal to 1.")

    requested_day = _parse_iso_date(day_text)
    resolved_symbol = _resolve_symbol(config, symbol)
    output_path = output or f"logs/{resolved_symbol}_{multiplier}minute_{requested_day.isoformat()}.csv"

    adapter = PolygonAdapter(config)
    rows = adapter.get_single_day_aggregate_rows(
        symbol=resolved_symbol,
        day=requested_day,
        multiplier=multiplier,
    )
    export_polygon_aggregate_rows(rows, output_path)
    print(f"Saved {len(rows)} rows to {output_path}")


def _resolve_symbol(config: AppConfig, symbol: str) -> str:
    if symbol:
        return symbol.upper()
    if config.app.symbols:
        return config.app.symbols[0].upper()
    raise SystemExit("A symbol is required for fetch-day when no symbols are configured.")


def _parse_iso_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise SystemExit(f"Invalid date '{value}'. Expected YYYY-MM-DD.") from exc


def _run_live_mode(
    config: AppConfig,
    logger: SQLiteLogger,
    poll_seconds: int,
    market_hours_only: bool = False,
) -> None:
    if not config.polygon.api_key:
        raise SystemExit("Polygon API key is required for live mode.")

    logger.initialize()
    adapter = PolygonAdapter(config)
    alerter = TelegramAlerter(config)
    run_id = logger.create_run(mode="live", config=config, source="polygon")
    seen_keys: set[tuple[str, str, str]] = set()
    enforce_market_hours = config.live.market_hours_only or market_hours_only
    market_timezone = ZoneInfo(config.app.market_timezone)
    market_open = parse_clock_time(config.live.market_open_time, field_name="live.market_open_time")
    market_close = parse_clock_time(config.live.market_close_time, field_name="live.market_close_time")

    if enforce_market_hours:
        print(
            "Live mode market-hours gate enabled for "
            f"{config.app.market_timezone}: {config.live.market_open_time}-{config.live.market_close_time}"
        )

    while True:
        cycle_now = datetime.now(tz=UTC)
        if enforce_market_hours and not is_within_market_hours(cycle_now, market_timezone, market_open, market_close):
            time.sleep(max(5, poll_seconds))
            continue

        all_evaluations: list[SetupEvaluation] = []
        all_alerts: list[AlertRecord] = []
        for symbol in config.app.symbols:
            end = cycle_now
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
