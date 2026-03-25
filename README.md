# Intraday Indicator Alert Bot

Replay-first Python bot for intraday QQQ/SPY signal evaluation using VWAP, EMA 9, SMA 15, SMA 30, RVGI, RVGI SMA, recent volume context, and optional 1m confirmation. The 5m SMA 15 / SMA 30 crossover is the primary trigger and the most important indicator in the stack.

## What v1 does
- Replays historical 1m candles from local CSV
- Resamples to 5m for primary decisions
- Treats the 5m SMA 15 / SMA 30 crossover as the primary trigger
- Grades bullish and bearish setups as `A`, `B`, or `C`
- Produces advisory strike bias recommendations
- Logs every 5m bull/bear evaluation to SQLite
- Exports review CSVs
- Formats and optionally sends Telegram alerts
- Includes a minimal Polygon historical/live polling adapter
- Includes a pragmatic Polygon bootstrap command for one-day minute aggregate downloads

## Primary trigger
- Bullish: on the 5m chart, `SMA 15` crosses above `SMA 30`
- Bearish: on the 5m chart, `SMA 15` crosses below `SMA 30`
- This crossover is the most important indicator in the system
- If the crossover appears while the active 5m candle is still printing, it can still be treated as a valid 5m trigger for live awareness
- 1m is secondary confirmation only and must not replace the 5m crossover logic

## Project layout
```text
src/
  config/
  models/
  data/
  indicators/
  signals/
  grading/
  alerts/
  storage/
  backtest/
  main.py
tests/
docs/
logs/
```

## Setup
1. Create a Python 3.11+ virtual environment.
2. Install dependencies:
   ```bash
   pip install -e .[dev]
   ```
3. Copy [`docs/config.example.toml`](docs/config.example.toml) to `config.toml` and update secrets/paths.

Secrets can also be provided by environment variables:
- `POLYGON_API_KEY`
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

## Replay input CSV
Required columns:

```text
timestamp,open,high,low,close,volume,symbol
```

Notes:
- `timestamp` should be ISO-8601 and timezone-aware when possible.
- Input candles are expected to be 1m bars.
- Replay treats 1m as canonical input and internally resamples to 5m bars for the primary SMA 15 / SMA 30 trigger.
- Replay also accepts legacy fixture names like `QQQ_5minute_2026-03-24.csv` and will use the matching `QQQ_1minute_2026-03-24.csv` file when present.

See [`tests/fixtures/sample_intraday.csv`](tests/fixtures/sample_intraday.csv) for an example.

## Commands
Initialize the SQLite schema:

```bash
signal-bot init-db --config config.toml
```

Run replay:

```bash
signal-bot replay --config config.toml --csv tests/fixtures/sample_intraday.csv
```

Run replay during the configured market window only:

```bash
signal-bot replay --config config.toml --csv tests/fixtures/sample_intraday.csv --market
```

Run replay and export reviewed rows:

```bash
signal-bot replay --config config.toml --csv tests/fixtures/sample_intraday.csv --export logs/replay_export.csv
```

Fetch a single day of Polygon minute data as replay-compatible candles:

```bash
signal-bot --config config.toml fetch-day -date {yyyy-mm-dd} --symbol QQQ
```

Without `--output`, the command writes to `tests/fixtures/{symbol}_1minute_{yyyy-mm-dd}.csv`.

If you want a custom target path instead:

```bash
signal-bot --config config.toml fetch-day -date {yyyy-mm-dd} --symbol QQQ --output logs/QQQ_1minute_{yyyy-mm-dd}.csv
```

If `--symbol` is omitted, the command uses the first configured symbol.

Run minimal live polling:

```bash
signal-bot live --config config.toml --poll-seconds 60
```

Restrict live polling to market hours in the configured market timezone:

```bash
signal-bot live --config config.toml --poll-seconds 60 --market
```

`--market-hours-only` still works as a compatibility alias.

Default market-hours window in config:

```toml
[live]
market_hours_only = true
market_open_time = "09:30"
market_close_time = "15:45"
```

This window is interpreted in `app.market_timezone`, which defaults to `America/New_York`.

Export prior SQLite evaluations to CSV:

```bash
signal-bot export-csv --config config.toml --output logs/evaluations.csv
```

## Grading summary
- `A`: fresh 5m SMA 15 / SMA 30 trigger, clean structure, aligned momentum, at least acceptable volume, and supportive 1m when required
- `B`: valid 5m crossover with constructive structure, but incomplete confirmation or mixed 1m support
- `C`: weak, conflicted, poorly confirmed, warmup-limited, or missing the fresh 5m crossover trigger

Guidance rules:
- Prefer `ATM` over OTM when uncertain
- Prefer `skip` over forcing weak setups
- 1m confirmation never overrides poor 5m structure
- 1m confirmation never replaces the 5m SMA 15 / SMA 30 crossover trigger

## SQLite outputs
Default tables:
- `runs`
- `evaluated_setups`
- `alerts`

Each 5m bar logs both bullish and bearish evaluations, even when no alert is emitted. Logged rows should explicitly capture the 5m SMA crossover state, such as `sma_cross_signal`.

## Testing
```bash
pytest
```

Tests cover:
- indicator behavior
- grading and strike bias rules
- alert formatting
- replay determinism
- Polygon bootstrap command behavior
- live market-hours gating
- replay market-hours gating


