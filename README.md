# Intraday Indicator Alert Bot

Replay-first Python bot for intraday QQQ/SPY signal evaluation using VWAP, EMA 9, SMA 15, SMA 30, RVGI, RVGI SMA, recent volume context, and optional 1m confirmation.

## What v1 does
- Replays historical 1m candles from local CSV
- Resamples to 5m for primary decisions
- Grades bullish and bearish setups as `A`, `B`, or `C`
- Produces advisory strike bias recommendations
- Logs every 5m bull/bear evaluation to SQLite
- Exports review CSVs
- Formats and optionally sends Telegram alerts
- Includes a minimal Polygon historical/live polling adapter
- Includes a pragmatic Polygon bootstrap command for one-day minute aggregate downloads

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
- Replay treats 1m as canonical input and internally resamples to closed 5m bars.

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

Fetch a single day of Polygon aggregate data with a fixed `minute` timespan:

```bash
signal-bot --config config.toml fetch-day -date {yyyy-mm-dd} -multiplier 1 --symbol QQQ --output logs/QQQ_5minute_{yyyy-mm-dd}.csv
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
- `A`: clean structure, aligned momentum, at least acceptable volume, and supportive 1m when required
- `B`: constructive structure with incomplete confirmation or mixed 1m support
- `C`: weak, conflicted, poorly confirmed, or warmup-limited setup

Guidance rules:
- Prefer `ATM` over OTM when uncertain
- Prefer `skip` over forcing weak setups
- 1m confirmation never overrides poor 5m structure

## SQLite outputs
Default tables:
- `runs`
- `evaluated_setups`
- `alerts`

Each 5m bar logs both bullish and bearish evaluations, even when no alert is emitted.

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
