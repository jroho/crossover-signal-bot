# AGENTS.md

## Name
Intraday Signal Bot

## Purpose
Build and maintain a Python alert bot for intraday options-oriented signal detection on QQQ, SPY, and optionally NVDA.

The bot does not place trades.
It watches market data, computes indicators, grades setups as A/B/C, and sends alerts with a suggested strike bias.

## Primary Goal
Detect bullish and bearish intraday setups using:
- VWAP
- EMA 9
- 5m SMA 15 / SMA 30 crossover as the primary signal
- SMA 15 level
- SMA 30 level
- RVGI
- RVGI SMA
- rolling volume comparison
- optional 1m confirmation and timing context

## Product Intent
This project is an alerting and signal-evaluation tool.
It is not an execution engine in v1.

The system should:
1. ingest historical and live candle data
2. calculate indicators consistently
3. evaluate bullish and bearish setups
4. assign a setup grade: A, B, or C
5. produce a strike bias recommendation
6. send alerts through Telegram
7. log every evaluated setup for later analysis and backtesting

## Non-Goals
- No brokerage execution in v1
- No Robinhood automation
- No automated order placement
- No options order routing
- No options PnL modeling in v1
- No overfitting to isolated historical sessions
- No unnecessary indicators beyond the defined stack

## Core Design Decisions
- Primary market data provider: Polygon
- Primary alert transport: Telegram Bot API
- Primary local storage: SQLite
- Secondary export format: CSV
- Initial development priority: replay/backtesting mode first
- Live mode should reuse the same signal and grading logic as replay mode
- Alpaca may be added later for paper trading or live execution, but is explicitly out of scope for v1

## Supported Symbols
Default watchlist:
- QQQ
- SPY

Optional later:
- NVDA

## Timeframes
- 5m is the primary decision timeframe
- 1m is an optional confirmation and timing timeframe

## Tech Stack
- Python 3.11+
- pandas
- numpy
- requests
- websockets and/or websocket-client
- sqlite3
- pydantic or dataclasses for typed models
- pytest for testing

Optional:
- ta library for indicator support if it reduces complexity without obscuring calculations

## Architecture Principles
- Keep the signal engine deterministic
- Use the same business logic in replay mode and live mode
- Separate data adapters from indicator logic
- Separate indicator logic from grading logic
- Separate grading logic from alert formatting and delivery
- Prefer explicit configuration over hard-coded thresholds
- Favor clarity and observability over cleverness

## Primary Modules
Suggested structure:

- src/
  - config/
  - models/
  - data/
  - indicators/
  - signals/
  - grading/
  - alerts/
  - storage/
  - backtest/
  - main.py

- tests/
- logs/
- docs/
- AGENTS.md

## Market Data Requirements
Use Polygon as the primary source for:
- historical minute candles
- live minute candles or trade aggregation
- symbol metadata as needed

The code should abstract market data behind an adapter interface so the signal engine is not tightly coupled to Polygon implementation details.

## Alert Requirements
Use Telegram Bot API for alert delivery.

Alerts should be concise, structured, and readable on mobile.
Each alert must include:
- symbol
- timestamp
- timeframe
- direction: bull or bear
- current price
- grade: A/B/C
- passed conditions
- weak or failed conditions
- volume quality
- higher timeframe agreement status
- strike bias recommendation
- plain-English reason

## Storage Requirements
Use SQLite as the primary structured store for:
- evaluated setups
- emitted alerts
- configuration snapshots if useful
- future trade review annotations if added later

Also support CSV export for:
- quick manual review
- spreadsheet analysis
- ad hoc backtesting inspection

## Indicator Definitions
Use these defaults unless explicitly overridden by config:
- EMA length: 9
- SMA fast: 15
- SMA slow: 30
- Primary trigger: 5m SMA 15 / SMA 30 crossover
- Bullish trigger: SMA 15 crosses above SMA 30 on the 5m candle
- Bearish trigger: SMA 15 crosses below SMA 30 on the 5m candle
- This 5m 15 / 30 crossover is the most important indicator in the system
- If the crossover happens while the active 5m candle is still printing, it still counts as a valid 5m trigger for live awareness
- 1m candles do not own the crossover logic; they are secondary confirmation only
- RVGI length: 10
- RVGI SMA length: 10
- volume comparison window: prior 5 candles minimum
- optional rolling volume average windows: 10 or 20 candles

## Signal Hierarchy
Always evaluate in this order:
1. market structure / bias
2. 5m SMA 15 / SMA 30 crossover trigger
3. momentum confirmation
4. volume confirmation
5. higher timeframe agreement
6. strike bias recommendation

No single oscillator should override poor price structure.
The 5m SMA 15 / SMA 30 crossover is the primary trigger and the most important indicator in the stack.

## Bullish Structure Rules
A bullish setup is structurally valid when most or all are true:
- 5m close > VWAP
- 5m close > EMA 9
- SMA 15 > SMA 30
- the primary bullish trigger is SMA 15 crossing above SMA 30 on the 5m candle
- if that crossover appears while the current 5m candle is still printing, it still counts as a 5m bullish trigger

## Bearish Structure Rules
A bearish setup is structurally valid when most or all are true:
- 5m close < VWAP
- 5m close < EMA 9
- SMA 15 < SMA 30
- the primary bearish trigger is SMA 15 crossing below SMA 30 on the 5m candle
- if that crossover appears while the current 5m candle is still printing, it still counts as a 5m bearish trigger

## RVGI Interpretation
Robinhood-style interpretation should be supported conceptually:
- RVGI is the fast line
- RVGI SMA is the smoothing/signal proxy

Bullish:
- RVGI > 0 is favorable
- RVGI SMA > 0 is favorable
- RVGI > RVGI SMA is stronger confirmation
- both rising is constructive even if crossover is incomplete

Bearish:
- RVGI < 0 is favorable
- RVGI SMA < 0 is favorable
- RVGI < RVGI SMA is stronger confirmation
- both falling is constructive even if crossover is incomplete

RVGI is a confirmation tool, not the sole trigger.

## Volume Filter
Use recent-candle context, not full-session visual guessing.

Required implementation:
- compare trigger candle volume to prior 5 candles

Optional implementation:
- compare trigger candle volume to rolling 10-candle average

Interpret volume as:
- strong: trigger volume is among top 2 of prior 5 candles, or > 120% of rolling average
- acceptable: around average, roughly 90% to 120% of rolling average
- weak: clearly below recent candles, or < 90% of rolling average

## Higher Timeframe Agreement
When enabled:
- 1m should generally agree with 5m direction
- for bullish setups, 1m price above VWAP and EMA 9 is supportive
- for bearish setups, 1m price below VWAP and EMA 9 is supportive

1m agreement adds confidence but does not override weak 5m structure.
1m is confirmation only and must not replace the 5m SMA 15 / SMA 30 crossover trigger.

## Nearby Structure Awareness
Design the grading engine so it can later incorporate nearby resistance/support checks.

In v1, this may be implemented as optional metadata or placeholder logic if reliable structure detection is not yet built.

## Setup Grading

### Grade A
Use Grade A when structure, timing, and momentum are all strong.
A fresh 5m SMA 15 / SMA 30 crossover should carry the most weight in the timing decision.

Bullish A:
- 5m SMA 15 crosses above SMA 30, or has just crossed on the active 5m candle
- 5m close > VWAP
- 5m close > EMA 9
- SMA 15 > SMA 30
- RVGI > RVGI SMA
- RVGI > 0
- trigger volume acceptable or strong
- optional 1m agreement present
- no obvious structural conflict if structure filter exists

Bearish A:
- 5m SMA 15 crosses below SMA 30, or has just crossed on the active 5m candle
- inverse of bullish A

Strike bias for Grade A:
- default: ATM
- allowed: +/-1 OTM
- +/-2 OTM only in rare expansion cases if enabled by config

### Grade B
Use Grade B when the setup is constructive but not fully confirmed.

Examples:
- price structure is good but RVGI crossover is incomplete
- the 5m 15 / 30 crossover is present, but other confirmations are only partial
- both RVGI and RVGI SMA are above 0 and rising, but RVGI is still below RVGI SMA
- volume is average, not strong
- 1m confirmation is mixed
- structure is good but not especially clean

Strike bias for Grade B:
- default: ATM
- conservative option: +/-1 ITM
- avoid +/-2 OTM

### Grade C
Use Grade C when the setup is weak, late, messy, conflicting, or low quality.

Examples:
- price chops around VWAP and EMA 9
- there is no fresh 5m 15 / 30 crossover trigger, or the crossover conflicts with the rest of the setup
- SMA relationship is weak, flat, or recently unstable
- RVGI is deteriorating
- trigger volume is weak
- higher timeframe disagrees
- structure is unclear or crowded

Strike bias for Grade C:
- default: skip
- optional soft review mode: ATM or +/-1 ITM only

## Strike Bias Logic
Strike bias is advisory only.

General logic:
- +/-1 OTM works best on strong A or A- style expansion setups
- ATM is the baseline recommendation for most valid setups
- +/-1 ITM is the safer recommendation when confirmation is weaker or the move may be smaller
- +/-2 OTM should be rare and only used when configured explicitly
- skip should be preferred over forcing weak setups

Do not present strike bias as certainty.
Always include a reason.

## Alert Payload Example
Example output:

QQQ 5m BULL ALERT
Price: 586.24
Grade: B
15 / 30 cross: bullish 5m trigger
VWAP: pass
EMA 9: pass
15 > 30: pass
RVGI > 0: pass
RVGI > RVGI SMA: no
Volume: acceptable
1m agreement: yes
Strike bias: ATM
Reason: bullish structure is intact, but momentum confirmation is incomplete.

## Logging Requirements
Log every evaluated setup, even if no alert is emitted.

Minimum fields:
- symbol
- datetime
- timeframe
- direction
- last_price
- vwap_relation
- ema9_relation
- sma15_value
- sma30_value
- sma_trend_relation
- sma_cross_signal
- rvgi
- rvgi_sma
- rvgi_vs_sma
- rvgi_sign
- volume
- recent_volume_avg
- volume_grade
- one_min_agreement
- grade
- strike_bias
- alert_sent
- rationale

## Replay / Backtesting Requirements
Replay mode is the first-class development target.

Replay mode must:
- load historical candles
- compute all indicators using the same logic as live mode
- evaluate the same grading rules
- produce the same alert payload shape
- log all evaluated signals
- support forward evaluation windows such as 3m, 5m, 10m, and 15m

Backtesting priority:
1. evaluate underlying directional signal quality
2. evaluate grading usefulness
3. evaluate strike-bias recommendation usefulness

Do not begin with full options PnL simulation.

## Live Mode Requirements
Live mode should:
- ingest Polygon live or near-live candle data
- evaluate signals on candle close by default
- send Telegram alerts
- reuse the same grading and signal engine used in replay mode

## Configuration Requirements
All thresholds should be configurable, including:
- volume thresholds
- grade thresholds
- whether 1m agreement is required
- whether Grade C soft alerts are allowed
- whether +/-2 OTM suggestions are ever allowed
- symbol list
- enabled timeframes

## Testing Requirements
Add tests for:
- indicator calculations where practical
- grading rules
- strike bias logic
- alert payload formatting
- replay-mode deterministic behavior

Use fixture-based tests with sample candle data.

## Initial Deliverables
1. project scaffold
2. configuration model
3. candle and signal data models
4. Polygon market data adapter interface
5. indicator calculation module
6. grading engine
7. strike bias recommendation logic
8. Telegram alert formatter and sender
9. SQLite logger
10. CSV export support
11. replay backtester
12. tests
13. README with setup and usage instructions

## Development Guidance
- Prefer ATM over OTM when uncertain
- Prefer skip over forcing a weak signal
- Keep functions small and composable
- Avoid hidden heuristics
- Make every alert explainable
- Keep v1 practical and easy to debug


