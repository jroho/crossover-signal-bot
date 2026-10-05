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
- Includes market data adapters for Alpaca (default) and Polygon behind one interface
- Includes a `fetch-day` command for one-day minute candle downloads from either provider

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
- `APCA_API_KEY_ID` and `APCA_API_SECRET_KEY` (Alpaca)
- `POLYGON_API_KEY`
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

## Market data providers
`[data] provider` selects `alpaca` or `polygon`. When it is empty, the bot uses whichever provider has credentials configured, preferring Alpaca.

Alpaca is the default because its free plan covers both needs of a 0DTE strategy:
- Backfills and `fetch-day` use the consolidated tape (`historical_feed = "sip"`). The free plan withholds the most recent 15 minutes of SIP data, so any request that reaches into that window is served from the live feed instead.
- Live polling uses the real-time IEX feed (`live_feed = "iex"`). IEX volume is a fraction of consolidated volume; the volume grade compares bars against their own recent history, so the ratio still works, but the thresholds should be checked in paper trading.

Polygon remains available for its deeper history. Its Starter and Developer tiers are 15-minute delayed, which is too slow for live 0DTE signals; use it for history only.

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

Replay always keeps the full 1m history, including premarket candles, so 5m indicators have full context. Alerts are emitted only for evaluations whose timestamps fall inside the configured market window.

Run replay and export reviewed rows:

```bash
signal-bot replay --config config.toml --csv tests/fixtures/sample_intraday.csv --export logs/replay_export.csv
```

Fetch a single day of minute data from the configured provider as replay-compatible candles:

```bash
signal-bot --config config.toml fetch-day -date {yyyy-mm-dd} --symbol QQQ
```

Without `--output`, the command writes to `tests/fixtures/{symbol}_1minute_{yyyy-mm-dd}.csv`.

If you want a custom target path instead:

```bash
signal-bot --config config.toml fetch-day -date {yyyy-mm-dd} --symbol QQQ --output logs/QQQ_1minute_{yyyy-mm-dd}.csv
```

If `--symbol` is omitted, the command uses the first configured symbol.

Pull history for backtesting (Alpaca only):

```bash
signal-bot --config config.toml pull-history --start 2025-10-01 --end 2026-10-02 --symbols QQQ SPY --out data
```

This writes three per-day sets under `data/` (gitignored):
- `underlying/<SYM>/<SYM>_1minute_<date>.csv`: replay-compatible 1m bars from the consolidated tape, premarket through after-hours
- `episodes/<SYM>/<SYM>_episodes_<date>.csv`: every regular-hours 5m SMA 15/30 cross that day, graded or not, with its direction, spot, 1m→5m lag, and the five candidate 0DTE contracts (ITM2, ITM1, ATM, OTM1, OTM2)
- `options/<SYM>/<SYM>_options_<date>.csv`: 1m bars for those contracts from 15 minutes before the first cross to the 4:15 PM ET option close

The pull is resumable (days already on disk are skipped), paced for the API rate limit, and logs per-day failures to `data/pull_errors.log` instead of stopping. `--stage underlying` or `--stage options` runs one half; the end date is capped at yesterday because the free plan withholds the newest consolidated data.

Simulate 0DTE option trades on the pulled history:

```bash
signal-bot --config config.toml simulate --data data --start 2025-10-01 --end 2026-10-02 --symbols QQQ SPY --targets 15,20,25,30,40,50 --stops 30 --labels ATM,ITM1,OTM1 --min-grade A --out logs/sim
```

How a trade is simulated:
- Entry is the first minute after the cross whose grade reaches `--min-grade` (within `--max-entry-delay` minutes of detection), filled at the next option bar's open plus `--slippage`. Episodes that never qualify are kept as `no_entry` rows so the funnel is visible.
- Exits are first-touch on 1m option bars: the `--stops` stop (checked before the target when both print in one bar), the `--targets` limit (filled at the limit price), the time rule (at `--check-minutes` exit if the option is at or below entry unless 2 of 3 trend checks hold: underlying beyond entry, 1m agreement, aligned 5m structure with a wider SMA gap; re-checked at `--recheck-minutes`), `--max-hold`, and `--flat-time` ET.
- Each run writes `trades.csv`, `exit_reasons.csv`, and summaries by target, grade, lag bucket, hour and direction with hit rates, expectancy per contract and profit factor.

Per-day evaluations are cached under `data/evaluations/` the first time a day is simulated (`--cache-only` builds the cache without trading), so sweeps over targets and stops re-run in seconds.

Day context and entry filters:
- `signal-bot fetch-vix` downloads CBOE's daily VIX history to `data/context/`. Each trade then carries the prior day's VIX regime (low / mid / high / extreme), the day's data trend bias (prior close vs. 20-day average plus 5-day return → bull / bear / neutral), whether the trade is aligned with it, and the opening-range turbulence (09:30–09:45 range vs. the trailing 20-day median; only applied to fills after 9:45).
- `--plain-a-max-lag 5` accepts a plain-A entry only when the 1m→5m cross lag is at most 5 minutes (A+ always qualifies); `--bull-requires-alignment` takes bull trades only on bull-bias days; `--skip-turbulent` skips turbulent days. Declined entries stay in `trades.csv` as `filtered_*` rows.
- Defaults reflect the Oct 2025–Aug 2026 backtest: +50 / −30 target and stop, no time rule, no entries after 13:00 ET. The policy that held out of sample is `--targets 30 --plain-a-max-lag 5 --bull-requires-alignment`.

Trade (paper by default):

```bash
signal-bot --config config.toml trade --dry-run          # live quotes, no orders, simulated fills
signal-bot --config config.toml trade                    # paper orders on the Alpaca paper account
signal-bot --config config.toml trade --bias bear        # override today's data bias from the headlines
```

Trade mode runs the same signal loop as `live`, then hands each new alertable evaluation to the execution engine:
- one trade per crossover episode and direction; ATM 0DTE contract for the signal's direction
- entry policy = the backtest's surviving rules: A+ (or A with a 1m→5m lag ≤ `plain_a_max_lag_min`), aligned with the day's trend bias (data-derived, or `daily_bias_override` / `--bias`), no entries after `last_entry_time`
- sizing against `risk_capital_usd`: one contract per `usd_per_contract`, doubled for aligned bears, capped by `max_contracts` and by `max_position_cost_pct` of capital
- entry as a limit at ask + `entry_limit_buffer`, canceled after `entry_timeout_minutes`; on fill a resting limit sell at `+target_pct`; the stop, `max_hold_minutes` and `flat_time` are enforced by closing the position
- halts on the daily loss limit, `max_losses_per_day`, the drawdown kill switch or a broker error; open positions are still managed to exit
- every decision and fill goes to `logs/live_trades.sqlite3` / `.csv` (`live_trades`, `live_events`); on restart the engine re-adopts journaled and broker-held positions

Real money needs `[trading] paper = false` **and** `--live`.

Live and trade modes evaluate the whole session from `[live] session_start_time` (default 04:00 ET) on every poll, not a sliding window: crossovers and the SMA-30 warm-up need the full day. On Alpaca the bars come from the consolidated feed up to 15 minutes ago and from IEX after that, with IEX volume scaled by the ratio measured where the two overlap. Trade mode also runs an account preflight at startup (paper/live keys must match the mode, account active, options level 2+) and checks buying power before each entry.

Daily routine from a Claude Code window in this repo: `/paper-trade` (or `/dry-run`) around 9:25 ET, optionally with `bull`, `bear` or `neutral` to override the data bias; `/trade-status` during the day; `/trade-stop` after 15:35 ET. These wrap `scripts/trade_start.ps1`, `trade_status.ps1` and `trade_stop.ps1`, which start the loop detached from the window.

Run minimal live polling:

```bash
signal-bot live --config config.toml --poll-seconds 60
```

Live alert delivery is automatically gated to the configured market window. Calculations still use the full fetched lookback so premarket context can inform the opening regular-hours signals.

Default market-hours window in config:

```toml
[live]
market_open_time = "09:30"
market_close_time = "15:45"
```

This window is interpreted in `app.market_timezone`, which defaults to `America/New_York`.

Export prior SQLite evaluations to CSV:

```bash
signal-bot export-csv --config config.toml --output logs/evaluations.csv
```

## Grading summary
- `A+`: everything in `A`, plus a cross no older than `grading.a_plus_max_cross_bars` (default 1), strong volume, RVGI expanding with the trade, and 1m agreement when enabled. The only grade that earns an OTM strike bias.
- `A`: a 5m SMA 15 / SMA 30 cross inside the fresh window (`grading.fresh_cross_max_bars`, default 3 bars), clean structure, aligned momentum, at least acceptable volume, and supportive 1m when required. Strike bias ATM.
- `B`: valid 5m crossover with constructive structure, but incomplete confirmation or mixed 1m support
- `C`: weak, conflicted, poorly confirmed, warmup-limited, or without a usable trigger: a cross older than the fresh window is `stale`, and a regime inferred from SMA levels with no observed cross this session is `derived`; neither triggers

Freshness, volume, and alert cadence:
- Cross state resets every session, so yesterday's crossover never triggers today.
- While a 5m candle is still printing, its volume is compared against the prior candles' volume through the same elapsed minute (like for like), not against full five-minute bars.
- Every evaluation logs `sma_cross_age_bars`, `sma_cross_lag_min` (minutes between the 1m SMA 15/30 cross and the 5m cross; positive means the 1m led), and `bar_minutes_elapsed`.
- One alert per crossover episode per direction; a later upgrade to a higher grade alerts once more, a downgrade never does.
- `grading.alert_grades` is a floor: `["A", "B"]` also alerts `A+`.
- Pop outcome thresholds live in `[outcomes]` and the realized-pop walk includes the 10m horizon.

Guidance rules:
- Prefer `ATM` over OTM when uncertain; OTM is reserved for `A+`
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
- Alpaca adapter bar mapping, feed selection, and provider resolution
- Polygon bootstrap command behavior
- live alert gating during market hours
- replay alert gating during market hours while preserving full indicator history



