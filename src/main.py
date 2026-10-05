from __future__ import annotations

import argparse
import sqlite3
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from src.alerts import AlertDeduper, TelegramAlerter, format_alert
from src.backtest import HistoryPuller, ReplayEngine, SimConfig, run_simulation, write_outputs
from src.backtest.context import fetch_vix_history
from src.backtest.policy_table import PolicyConfig, build_policy_table
from src.backtest.simulate import TIME_RULE_MODES
from src.backtest.strikes import STRIKE_LABELS
from src.config import AppConfig, load_config
from src.data import AlpacaAdapter, MarketDataAdapter, PolygonAdapter
from src.execution import AlpacaBroker, DryRunBroker, ExecutionEngine, TradeJournal, build_live_context
from src.grading import grade_is_alertable
from src.market_hours import is_within_market_hours, parse_clock_time
from src.models import AlertRecord, Direction, Grade, OutcomeGrade, OutcomeResult, SetupEvaluation, StrikeBias, Timeframe
from src.signals import evaluate_symbol
from src.storage import (
    SQLiteLogger,
    aggregate_rows_to_replay_rows,
    export_evaluations_to_csv,
    export_replay_candle_rows,
)

SUPPORTED_PROVIDERS = ("alpaca", "polygon")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Intraday indicator alert bot")
    parser.add_argument("--config", default="docs/config.example.toml", help="Path to TOML config file")
    subparsers = parser.add_subparsers(dest="command", required=True)

    replay = subparsers.add_parser("replay", help="Run replay mode from local CSV")
    replay.add_argument("--config", default=argparse.SUPPRESS, help="Path to TOML config file")
    replay.add_argument("--csv", default="", help="Replay CSV path")
    replay.add_argument("--export", default="", help="Optional evaluation CSV export path")

    live = subparsers.add_parser("live", help="Run minimal live polling with the configured market data provider")
    live.add_argument("--config", default=argparse.SUPPRESS, help="Path to TOML config file")
    live.add_argument("--poll-seconds", type=int, default=60, help="Polling interval in seconds")

    fetch_day = subparsers.add_parser("fetch-day", help="Fetch one day of replay-compatible minute candles to CSV")
    fetch_day.add_argument("--config", default=argparse.SUPPRESS, help="Path to TOML config file")
    fetch_day.add_argument("-date", "--date", dest="day", required=True, help="Trading day in YYYY-MM-DD format")
    fetch_day.add_argument("--symbol", default="", help="Ticker symbol; defaults to the first configured symbol")
    fetch_day.add_argument("--output", default="", help="Optional CSV output path")

    pull_history = subparsers.add_parser("pull-history", help="Pull 1m underlying history and 0DTE option bars per cross episode (Alpaca)")
    pull_history.add_argument("--config", default=argparse.SUPPRESS, help="Path to TOML config file")
    pull_history.add_argument("--start", required=True, help="First trading day, YYYY-MM-DD")
    pull_history.add_argument("--end", default="", help="Last trading day, YYYY-MM-DD; defaults to yesterday")
    pull_history.add_argument("--symbols", nargs="*", default=[], help="Symbols; defaults to the configured list")
    pull_history.add_argument("--out", default="data", help="Output directory")
    pull_history.add_argument("--stage", choices=["all", "underlying", "options"], default="all", help="Which stage to run")
    pull_history.add_argument("--sleep-seconds", type=float, default=0.35, help="Pause between API requests")

    simulate = subparsers.add_parser("simulate", help="Simulate 0DTE option trades on pulled history with target/stop/time-rule exits")
    simulate.add_argument("--config", default=argparse.SUPPRESS, help="Path to TOML config file")
    simulate.add_argument("--data", default="data", help="Directory written by pull-history")
    simulate.add_argument("--out", default="logs/sim", help="Output directory for trades and summaries")
    simulate.add_argument("--start", required=True, help="First trading day, YYYY-MM-DD")
    simulate.add_argument("--end", required=True, help="Last trading day, YYYY-MM-DD")
    simulate.add_argument("--symbols", nargs="*", default=[], help="Symbols; defaults to the configured list")
    simulate.add_argument("--targets", default="30,40,50", help="Comma-separated profit targets in percent")
    simulate.add_argument("--stops", default="30", help="Comma-separated stop losses in percent")
    simulate.add_argument("--labels", default="ATM", help="Comma-separated strike labels: ITM2,ITM1,ATM,OTM1,OTM2")
    simulate.add_argument("--min-grade", default="A", help="Lowest grade that triggers an entry: A+, A, B or C")
    simulate.add_argument("--slippage", type=float, default=0.02, help="Dollars per share paid on each side")
    simulate.add_argument("--max-entry-delay", type=int, default=15, help="Minutes after detection to wait for an alertable grade")
    simulate.add_argument("--last-entry", default="13:00", help="No entries after this market time (HH:MM); 'none' disables the cutoff")
    simulate.add_argument("--time-rule", choices=list(TIME_RULE_MODES), default="off", help="Time-rule mode: off, option (exit if option <= entry and trend faded) or underlying (exit whenever trend faded)")
    simulate.add_argument("--check-minutes", type=int, default=15, help="First time-rule check, minutes after fill")
    simulate.add_argument("--recheck-minutes", type=int, default=25, help="Second time-rule check, minutes after fill")
    simulate.add_argument("--max-hold", type=int, default=45, help="Hard maximum hold in minutes")
    simulate.add_argument("--flat-time", default="15:35", help="Close everything at this market time (HH:MM)")
    simulate.add_argument("--plain-a-max-lag", default="", help="Accept plain-A entries only when the 1m->5m cross lag is at most this many minutes (A+ always qualifies); empty = no lag filter")
    simulate.add_argument("--bull-requires-alignment", action="store_true", help="Take bull trades only on days whose data trend bias is bullish")
    simulate.add_argument("--require-alignment", action="store_true", help="Take trades only when they agree with the day's data trend bias (both directions; no trades on neutral days)")
    simulate.add_argument("--skip-turbulent", action="store_true", help="Skip entries on days whose opening range reads turbulent")
    simulate.add_argument("--trail-triggers", default="", help="Comma-separated gains (percent) that arm a trailing stop; empty = no trailing")
    simulate.add_argument("--trail", type=float, default=15.0, help="Trailing stop distance below the high, percent of the high")
    simulate.add_argument("--cache-only", action="store_true", help="Only build the per-day evaluation cache")

    trade = subparsers.add_parser("trade", help="Run the live signal loop with option execution (paper by default)")
    trade.add_argument("--config", default=argparse.SUPPRESS, help="Path to TOML config file")
    trade.add_argument("--poll-seconds", type=int, default=20, help="Polling interval in seconds")
    trade.add_argument("--dry-run", action="store_true", help="Use live quotes but send no orders; fills are simulated")
    trade.add_argument("--live", action="store_true", help="Required, together with [trading] paper = false, to send real-money orders")
    trade.add_argument("--bias", default="", help="Override today's bias for all symbols: bull, bear or neutral")

    fetch_vix = subparsers.add_parser("fetch-vix", help="Download CBOE's daily VIX history for regime context")
    fetch_vix.add_argument("--config", default=argparse.SUPPRESS, help="Path to TOML config file")
    fetch_vix.add_argument("--out", default="data/context/VIX_History.csv", help="Where to write the CSV")

    policy = subparsers.add_parser("policy-table", help="Turn simulated trades into per-bucket trade/skip decisions and sizes")
    policy.add_argument("--config", default=argparse.SUPPRESS, help="Path to TOML config file")
    policy.add_argument("--trades", default="logs/sim/trades.csv", help="trades.csv written by simulate")
    policy.add_argument("--keys", default="entry_grade,time_bucket", help="Comma-separated bucket columns")
    policy.add_argument("--target", type=float, default=None, help="Only use rows with this target_pct")
    policy.add_argument("--stop", type=float, default=None, help="Only use rows with this stop_pct")
    policy.add_argument("--label", default="", help="Only use rows with this strike label")
    policy.add_argument("--trail-trigger", default="", help="Only use rows with this trailing trigger ('none' for fixed-stop rows)")
    policy.add_argument("--min-trades", type=int, default=20, help="Buckets below this stay in 'learn'")
    policy.add_argument("--account", type=float, default=500.0, help="Account size used for sizing")
    policy.add_argument("--max-contracts", type=int, default=2, help="Size cap per trade")
    policy.add_argument("--out", default="logs/sim/policy.csv", help="Where to write the table")

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
        )
        print(f"Replay complete: {len(result.evaluations)} evaluations, {len(result.alerts)} alerts, run_id={result.run_id}")
        return

    if args.command == "fetch-day":
        _run_fetch_day_command(
            config=config,
            symbol=args.symbol,
            day_text=args.day,
            output=args.output,
        )
        return

    if args.command == "pull-history":
        _run_pull_history_command(
            config=config,
            start_text=args.start,
            end_text=args.end,
            symbols=args.symbols,
            out_dir=args.out,
            stage=args.stage,
            sleep_seconds=args.sleep_seconds,
        )
        return

    if args.command == "simulate":
        _run_simulate_command(config=config, args=args)
        return

    if args.command == "policy-table":
        _run_policy_table_command(args)
        return

    if args.command == "fetch-vix":
        path = fetch_vix_history(args.out)
        print(f"Saved VIX history to {path}")
        return

    if args.command == "trade":
        _run_trade_mode(config=config, poll_seconds=args.poll_seconds, dry_run=args.dry_run, live_flag=args.live, bias_override=args.bias)
        return

    if args.command == "export-csv":
        export_evaluations_to_csv(
            _read_evaluations_from_db(config.storage.sqlite_path),
            args.output,
            market_timezone=config.app.market_timezone,
        )
        print(f"Exported evaluations to {args.output}")
        return

    if args.command == "live":
        _run_live_mode(
            config=config,
            logger=logger,
            poll_seconds=args.poll_seconds,
        )
        return

    parser.error(f"Unknown command: {args.command}")


def _resolve_provider(config: AppConfig) -> str:
    provider = config.data.provider.strip().lower()
    if provider:
        if provider not in SUPPORTED_PROVIDERS:
            raise SystemExit(f"Unsupported data provider '{config.data.provider}'. Choose one of: {', '.join(SUPPORTED_PROVIDERS)}.")
        return provider
    if config.alpaca.api_key_id and config.alpaca.api_secret_key:
        return "alpaca"
    if config.polygon.api_key:
        return "polygon"
    raise SystemExit("No market data provider configured. Set [data] provider and the matching Alpaca or Polygon credentials.")


def _build_market_data_adapter(config: AppConfig, provider: str, command: str) -> MarketDataAdapter:
    if provider == "alpaca":
        if not (config.alpaca.api_key_id and config.alpaca.api_secret_key):
            raise SystemExit(f"Alpaca API key ID and secret key are required for {command}.")
        return AlpacaAdapter(config)
    if not config.polygon.api_key:
        raise SystemExit(f"Polygon API key is required for {command}.")
    return PolygonAdapter(config)


def _run_fetch_day_command(
    config: AppConfig,
    symbol: str,
    day_text: str,
    output: str,
) -> None:
    provider = _resolve_provider(config)
    adapter = _build_market_data_adapter(config, provider, command="fetch-day")

    requested_day = _parse_iso_date(day_text)
    resolved_symbol = _resolve_symbol(config, symbol)
    output_path = output or _build_default_fetch_day_output_path(resolved_symbol, requested_day)

    rows = adapter.get_single_day_aggregate_rows(
        symbol=resolved_symbol,
        day=requested_day,
        multiplier=1,
    )
    replay_rows = aggregate_rows_to_replay_rows(rows, resolved_symbol)
    export_replay_candle_rows(replay_rows, output_path)
    print(f"Saved {len(rows)} rows to {output_path}")


def _run_pull_history_command(
    *,
    config: AppConfig,
    start_text: str,
    end_text: str,
    symbols: list[str],
    out_dir: str,
    stage: str,
    sleep_seconds: float,
) -> None:
    provider = _resolve_provider(config)
    if provider != "alpaca":
        raise SystemExit("pull-history needs the Alpaca provider; option history is not available from Polygon in this bot.")
    adapter = _build_market_data_adapter(config, provider, command="pull-history")
    assert isinstance(adapter, AlpacaAdapter)

    start = _parse_iso_date(start_text)
    # Yesterday at the latest: the free plan withholds the newest 15 minutes of consolidated data.
    latest_allowed = datetime.now(tz=ZoneInfo(config.app.market_timezone)).date() - timedelta(days=1)
    end = min(_parse_iso_date(end_text), latest_allowed) if end_text else latest_allowed
    if end < start:
        raise SystemExit(f"End date {end} is before start date {start}.")
    resolved_symbols = [symbol.upper() for symbol in (symbols or config.app.symbols)]
    if not resolved_symbols:
        raise SystemExit("No symbols to pull.")

    puller = HistoryPuller(config, adapter, out_dir, sleep_seconds=sleep_seconds)
    print(f"Pulling {', '.join(resolved_symbols)} from {start} to {end} into {out_dir} (stage={stage})")
    if stage in {"all", "underlying"}:
        for symbol in resolved_symbols:
            written = puller.pull_underlying(symbol, start, end)
            print(f"{symbol}: wrote {len(written)} new underlying day files")
    if stage in {"all", "options"}:
        for symbol in resolved_symbols:
            written = puller.pull_options(symbol, start, end)
            print(f"{symbol}: wrote {len(written)} new option day files")


def _run_simulate_command(*, config: AppConfig, args: argparse.Namespace) -> None:
    start = _parse_iso_date(args.start)
    end = _parse_iso_date(args.end)
    if end < start:
        raise SystemExit(f"End date {end} is before start date {start}.")
    symbols = [symbol.upper() for symbol in (args.symbols or config.app.symbols)]
    labels = tuple(item.strip().upper() for item in args.labels.split(",") if item.strip())
    unknown = [label for label in labels if label not in STRIKE_LABELS]
    if unknown:
        raise SystemExit(f"Unknown strike labels {unknown}; choose from {', '.join(STRIKE_LABELS)}.")
    try:
        Grade(args.min_grade)
    except ValueError as exc:
        raise SystemExit(f"Unknown grade '{args.min_grade}'; choose A+, A, B or C.") from exc
    targets = [float(item) for item in args.targets.split(",") if item.strip()]
    stops = [float(item) for item in args.stops.split(",") if item.strip()]
    # "none" (or 0) in the list means a plain fixed-stop policy, so one run can compare both.
    trail_triggers: list[float | None] = [
        None if item.strip().lower() in {"none", "0"} else float(item)
        for item in args.trail_triggers.split(",")
        if item.strip()
    ] or [None]
    grid = [
        SimConfig(
            target_pct=target,
            stop_pct=stop,
            slippage=args.slippage,
            min_grade=args.min_grade,
            max_entry_delay_min=args.max_entry_delay,
            last_entry_time=None if args.last_entry.lower() == "none" else args.last_entry,
            time_rule_mode=args.time_rule,
            check_minutes=args.check_minutes,
            recheck_minutes=args.recheck_minutes,
            max_hold_minutes=args.max_hold,
            flat_time=args.flat_time,
            labels=labels,
            trail_trigger_pct=trail_trigger,
            trail_pct=args.trail,
            plain_a_max_lag_min=float(args.plain_a_max_lag) if args.plain_a_max_lag else None,
            bull_requires_alignment=args.bull_requires_alignment,
            require_alignment=args.require_alignment,
            skip_turbulent=args.skip_turbulent,
        )
        for target in targets
        for stop in stops
        for trail_trigger in trail_triggers
    ]
    trades = run_simulation(config, args.data, symbols, start, end, grid, cache_only=args.cache_only)
    if args.cache_only:
        print("Evaluation cache built.")
        return
    write_outputs(trades, args.out)


def _run_policy_table_command(args: argparse.Namespace) -> None:
    import pandas as pd

    trades_path = Path(args.trades)
    if not trades_path.exists():
        raise SystemExit(f"Trades file not found: {trades_path}")
    trades = pd.read_csv(trades_path)
    if args.target is not None:
        trades = trades[trades["target_pct"] == args.target]
    if args.stop is not None:
        trades = trades[trades["stop_pct"] == args.stop]
    if args.label:
        trades = trades[trades["label"] == args.label.upper()]
    if args.trail_trigger:
        if args.trail_trigger.lower() == "none":
            trades = trades[trades["trail_trigger_pct"].isna()]
        else:
            trades = trades[trades["trail_trigger_pct"] == float(args.trail_trigger)]
    keys = [item.strip() for item in args.keys.split(",") if item.strip()]
    missing = [key for key in keys if key not in trades.columns]
    if missing:
        raise SystemExit(f"Unknown bucket columns: {missing}")
    table = build_policy_table(
        trades,
        keys,
        PolicyConfig(min_trades=args.min_trades, account_usd=args.account, max_contracts=args.max_contracts),
    )
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    table.to_csv(out_path, index=False)
    print(f"Wrote {len(table)} buckets to {out_path}")
    if not table.empty:
        print(table.to_string(index=False))


def _build_default_fetch_day_output_path(symbol: str, day: date) -> Path:
    return Path("tests") / "fixtures" / f"{symbol.upper()}_1minute_{day.isoformat()}.csv"


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
) -> None:
    provider = _resolve_provider(config)
    adapter = _build_market_data_adapter(config, provider, command="live mode")

    logger.initialize()
    alerter = TelegramAlerter(config)
    run_id = logger.create_run(mode="live", config=config, source=provider)
    seen_keys: set[tuple[str, str, str]] = set()
    deduper = AlertDeduper()
    market_timezone = ZoneInfo(config.app.market_timezone)
    market_open = parse_clock_time(config.live.market_open_time, field_name="live.market_open_time")
    market_close = parse_clock_time(config.live.market_close_time, field_name="live.market_close_time")

    print(
        f"Live data provider: {provider}. Alert window uses "
        f"{config.app.market_timezone}: {config.live.market_open_time}-{config.live.market_close_time}. "
        "Calculations keep full fetched history; alert delivery is gated to that window."
    )

    while True:
        cycle_now = datetime.now(tz=UTC)
        can_emit_alerts = is_within_market_hours(cycle_now, market_timezone, market_open, market_close)

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
                if (
                    can_emit_alerts
                    and grade_is_alertable(evaluation.grade, config.grading.alert_grades)
                    and evaluation.strike_bias.value != "skip"
                    and deduper.should_alert(evaluation)
                ):
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


def _run_trade_mode(*, config: AppConfig, poll_seconds: int, dry_run: bool, live_flag: bool, bias_override: str) -> None:
    provider = _resolve_provider(config)
    if provider != "alpaca":
        raise SystemExit("trade mode needs the Alpaca provider for data and execution.")
    adapter = _build_market_data_adapter(config, provider, command="trade mode")
    assert isinstance(adapter, AlpacaAdapter)

    real_money = not config.trading.paper
    if real_money and not live_flag:
        raise SystemExit("[trading] paper = false requires the --live flag to send real-money orders.")
    if dry_run:
        mode = "dry-run"
    elif real_money:
        mode = "live"
    else:
        mode = "paper"

    alpaca_broker = AlpacaBroker(config)
    broker = DryRunBroker(alpaca_broker, equity=config.trading.risk_capital_usd) if dry_run else alpaca_broker
    journal = TradeJournal(config.trading.journal_sqlite_path, config.trading.journal_csv_path)
    engine = ExecutionEngine(config, broker, journal, mode=mode)
    logger = SQLiteLogger(config.storage.sqlite_path)
    logger.initialize()
    run_id = logger.create_run(mode=f"trade-{mode}", config=config, source=provider)
    market_timezone = ZoneInfo(config.app.market_timezone)
    override = bias_override or config.trading.daily_bias_override or None

    print(
        f"Trade mode: {mode}. Risk capital ${config.trading.risk_capital_usd:.0f}, target +{config.trading.target_pct:.0f}% / stop -{config.trading.stop_pct:.0f}%, "
        f"no entries after {config.trading.last_entry_time} ET, flat at {config.trading.flat_time} ET."
    )

    session_date = None
    seen_keys: set[tuple[str, str, str]] = set()
    while True:
        now = datetime.now(tz=UTC)
        today = now.astimezone(market_timezone).date()
        if today != session_date:
            session_date = today
            seen_keys.clear()
            engine.new_session(now)
            for symbol in config.app.symbols:
                try:
                    context = build_live_context(config, adapter, symbol, today, override=override)
                except Exception as exc:  # noqa: BLE001 - trade without context rather than crash, but say so
                    print(f"{symbol}: context unavailable ({type(exc).__name__}: {exc}); alignment filter will decline everything")
                    continue
                engine.set_context(symbol, context, now)
            engine.reconcile(now)

        try:
            for symbol in config.app.symbols:
                start = now - timedelta(minutes=config.live.lookback_minutes)
                candles = adapter.get_historical_candles(symbol, timeframe=Timeframe.ONE_MINUTE, start=start, end=now)
                evaluations, _, _ = evaluate_symbol(candles, config)
                if not evaluations:
                    continue
                latest_timestamp = max(item.timestamp for item in evaluations)
                fresh = []
                for evaluation in evaluations:
                    if evaluation.timestamp != latest_timestamp:
                        continue
                    key = (evaluation.symbol, evaluation.timestamp.isoformat(), evaluation.direction.value)
                    if key in seen_keys:
                        continue
                    seen_keys.add(key)
                    fresh.append(evaluation)
                if fresh:
                    logger.log_evaluations(run_id, fresh)
                    engine.on_evaluations(fresh, now)
            engine.on_tick(now)
        except Exception as exc:  # noqa: BLE001 - keep managing open positions through transient errors
            print(f"[{now.astimezone(market_timezone):%H:%M:%S}] loop error: {type(exc).__name__}: {exc}")
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
                sma_cross_signal=row["sma_cross_signal"] if "sma_cross_signal" in row.keys() else "none",
                sma_cross_status=row["sma_cross_status"] if "sma_cross_status" in row.keys() else "none",
                sma_cross_time=datetime.fromisoformat(row["sma_cross_time"]) if ("sma_cross_time" in row.keys() and row["sma_cross_time"]) else None,
                sma15_slope=row["sma15_slope"] if "sma15_slope" in row.keys() else None,
                sma30_slope=row["sma30_slope"] if "sma30_slope" in row.keys() else None,
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
                forward_return_30m=row["forward_return_30m"] if "forward_return_30m" in row.keys() else None,
                pop_outcome=OutcomeResult(row["pop_outcome"]) if ("pop_outcome" in row.keys() and row["pop_outcome"]) else None,
                pop_outcome_horizon=row["pop_outcome_horizon"] if ("pop_outcome_horizon" in row.keys() and row["pop_outcome_horizon"]) else None,
                pop_grade=OutcomeGrade(row["pop_grade"]) if ("pop_grade" in row.keys() and row["pop_grade"]) else None,
                sma_cross_age_bars=row["sma_cross_age_bars"] if "sma_cross_age_bars" in row.keys() else None,
                sma_cross_lag_min=row["sma_cross_lag_min"] if "sma_cross_lag_min" in row.keys() else None,
                bar_minutes_elapsed=row["bar_minutes_elapsed"] if "bar_minutes_elapsed" in row.keys() else None,
            )
        )
    return evaluations


if __name__ == "__main__":
    main()

