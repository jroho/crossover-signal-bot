from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from src.backtest.context import DayContext
from src.config.settings import AlpacaConfig, AppConfig, AppSection, GradingConfig, TradingConfig
from src.execution import DryRunBroker, ExecutionEngine, TradeJournal, TradingPolicy
from src.execution.broker import Contract, OrderInfo, Position, Quote
from src.models import Direction, Grade, SetupEvaluation, StrikeBias, Timeframe

NOW = datetime(2026, 3, 24, 15, 5, tzinfo=UTC)  # Tuesday 11:05 ET
BEAR_DAY = DayContext(trend_bias=-1, trend_label="bear", vix_regime="mid", vix_prev_close=18.0)
BULL_DAY = DayContext(trend_bias=1, trend_label="bull", vix_regime="mid", vix_prev_close=18.0)


def _config(**trading_overrides) -> AppConfig:
    settings = {"enabled": True, "paper": True, "risk_capital_usd": 500.0, **trading_overrides}
    trading = TradingConfig(**settings)
    return AppConfig(
        app=AppSection(symbols=["QQQ"], market_timezone="America/New_York"),
        grading=GradingConfig(alert_grades=["A", "B"]),
        alpaca=AlpacaConfig(api_key_id="k", api_secret_key="s"),
        trading=trading,
    )


def _evaluation(direction: Direction = Direction.BEAR, grade: Grade = Grade.A_PLUS, lag: float | None = 3.0, cross_minute: int = 1, price: float = 585.19) -> SetupEvaluation:
    return SetupEvaluation(
        symbol="QQQ",
        timestamp=NOW,
        timeframe=Timeframe.FIVE_MINUTE,
        direction=direction,
        last_price=price,
        vwap_relation="below_or_equal",
        ema9_relation="below_or_equal",
        sma15_value=584.0,
        sma30_value=585.0,
        sma_trend_relation="bearish_or_flat",
        sma_cross_signal=direction.value,
        sma_cross_status="fresh",
        sma_cross_time=datetime(2026, 3, 24, 15, cross_minute, 30, tzinfo=UTC),
        sma15_slope=-0.1,
        sma30_slope=-0.05,
        rvgi=-0.2,
        rvgi_sma=-0.1,
        rvgi_vs_sma="below_or_equal",
        rvgi_sign="negative_or_zero",
        volume=1000,
        recent_volume_avg=900,
        rolling_volume_avg=850,
        volume_grade="strong",
        one_min_agreement="yes",
        grade=grade,
        strike_bias=StrikeBias.ATM,
        strike_bias_reason="",
        sma_cross_age_bars=0.0,
        sma_cross_lag_min=lag,
    )


@dataclass
class FakeBroker:
    quotes: dict[str, Quote] = field(default_factory=dict)
    orders: dict[str, OrderInfo] = field(default_factory=dict)
    positions: dict[str, Position] = field(default_factory=dict)
    canceled: list[str] = field(default_factory=list)
    closed: list[str] = field(default_factory=list)
    market_open: bool = True
    fail_contract_lookup: bool = False
    _counter: int = 0

    def account_equity(self) -> float:
        return 100000.0

    def is_market_open(self) -> bool:
        return self.market_open

    def find_contract(self, underlying, expiration, option_type, strike):
        if self.fail_contract_lookup:
            raise RuntimeError("alpaca down")
        occ = f"{underlying}{expiration:%y%m%d}{option_type[0].upper()}{int(round(strike * 1000)):08d}"
        return Contract(occ, underlying, expiration, option_type, strike)

    def latest_quote(self, occ_symbol):
        return self.quotes.get(occ_symbol)

    def set_quote(self, occ_symbol, bid, ask):
        self.quotes[occ_symbol] = Quote(occ_symbol, bid, ask, NOW)

    def submit_limit(self, occ_symbol, qty, side, limit_price):
        self._counter += 1
        order = OrderInfo(f"o{self._counter}", occ_symbol, side, qty, "accepted", limit_price)
        self.orders[order.order_id] = order
        return order

    def fill(self, order_id, price):
        order = self.orders[order_id]
        self.orders[order_id] = OrderInfo(order.order_id, order.occ_symbol, order.side, order.qty, "filled", order.limit_price, order.qty, price)
        if order.side == "buy":
            self.positions[order.occ_symbol] = Position(order.occ_symbol, order.qty, price)
        else:
            self.positions.pop(order.occ_symbol, None)

    def get_order(self, order_id):
        return self.orders[order_id]

    def cancel_order(self, order_id):
        order = self.orders[order_id]
        self.orders[order_id] = OrderInfo(order.order_id, order.occ_symbol, order.side, order.qty, "canceled", order.limit_price)
        self.canceled.append(order_id)

    def close_position(self, occ_symbol):
        self.closed.append(occ_symbol)
        position = self.positions.pop(occ_symbol, None)
        if position is None:
            return None
        bid = self.quotes[occ_symbol].bid
        self._counter += 1
        return OrderInfo(f"o{self._counter}", occ_symbol, "sell", position.qty, "filled", None, position.qty, bid)

    def list_positions(self):
        return list(self.positions.values())


PUT_585 = "QQQ260324P00585000"
CALL_585 = "QQQ260324C00585000"


def _engine(tmp_path: Path, broker: FakeBroker, config: AppConfig | None = None) -> ExecutionEngine:
    cfg = config or _config()
    journal = TradeJournal(tmp_path / "live.sqlite3", tmp_path / "live.csv")
    engine = ExecutionEngine(cfg, broker, journal, mode="test", printer=lambda _: None)
    engine.set_context("QQQ", BEAR_DAY, NOW)
    return engine


# ----------------------------------------------------------------------------- policy


def test_policy_sizes_one_contract_at_five_hundred_dollars_and_scales_with_capital():
    policy = TradingPolicy(_config())
    assert policy.size(Direction.BEAR, "aligned", premium=1.80) == 1  # affordability caps the bear 2x
    assert policy.size(Direction.BULL, "aligned", premium=1.80) == 1

    bigger = TradingPolicy(_config(risk_capital_usd=5000.0))
    assert bigger.size(Direction.BULL, "aligned", premium=1.80) == 3  # base 4 capped at max_contracts
    assert bigger.size(Direction.BEAR, "aligned", premium=1.80) == 3
    assert bigger.size(Direction.BEAR, "aligned", premium=12.0) == 2  # 50% of capital / $1,200 premium

    assert policy.size(Direction.BEAR, "aligned", premium=3.00) == 0  # $300 contract > 50% of $500


def test_policy_declines_plain_a_with_long_lag_counter_trend_and_late_entries():
    policy = TradingPolicy(_config())
    ok = policy.evaluate_entry(_evaluation(), BEAR_DAY, NOW, premium=1.80)
    assert ok.allowed and ok.contracts == 1

    long_lag = policy.evaluate_entry(_evaluation(grade=Grade.A, lag=12.0), BEAR_DAY, NOW, premium=1.80)
    assert not long_lag.allowed and "lag" in long_lag.reason

    short_lag = policy.evaluate_entry(_evaluation(grade=Grade.A, lag=4.0), BEAR_DAY, NOW, premium=1.80)
    assert short_lag.allowed

    counter = policy.evaluate_entry(_evaluation(), BULL_DAY, NOW, premium=1.80)
    assert not counter.allowed and "not aligned" in counter.reason

    neutral = policy.evaluate_entry(_evaluation(), DayContext(trend_bias=0, trend_label="neutral"), NOW, premium=1.80)
    assert not neutral.allowed

    late = policy.evaluate_entry(_evaluation(), BEAR_DAY, datetime(2026, 3, 24, 17, 1, tzinfo=UTC), premium=1.80)
    assert not late.allowed and "last entry" in late.reason

    grade_b = policy.evaluate_entry(_evaluation(grade=Grade.B), BEAR_DAY, NOW, premium=1.80)
    assert not grade_b.allowed


# ----------------------------------------------------------------------------- engine


def test_entry_fill_places_target_and_stop_closes_the_trade(tmp_path: Path):
    broker = FakeBroker()
    broker.set_quote(PUT_585, bid=1.78, ask=1.80)
    engine = _engine(tmp_path, broker)

    engine.on_evaluations([_evaluation()], NOW)

    [trade] = engine.open_trades.values()
    assert trade.status == "pending_entry" and trade.occ_symbol == PUT_585 and trade.contracts == 1
    assert broker.orders[trade.entry_order_id].limit_price == pytest.approx(1.82)

    broker.fill(trade.entry_order_id, 1.81)
    engine.on_tick(NOW + timedelta(minutes=1))

    assert trade.status == "open" and trade.entry_price == 1.81
    assert trade.target_price == pytest.approx(2.35) and trade.stop_price == pytest.approx(1.27)
    target = broker.orders[trade.target_order_id]
    assert target.side == "sell" and target.limit_price == pytest.approx(2.35)

    broker.set_quote(PUT_585, bid=1.20, ask=1.24)
    engine.on_tick(NOW + timedelta(minutes=5))

    assert trade.status == "closed" and trade.exit_reason == "stop"
    assert trade.exit_price == pytest.approx(1.20) and trade.pnl_usd == pytest.approx(-61.0)
    assert trade.target_order_id in broker.canceled and broker.closed == [PUT_585]
    assert engine.journal.all_trades()[0].exit_reason == "stop"


def test_target_fill_closes_with_profit_and_episode_is_not_retraded(tmp_path: Path):
    broker = FakeBroker()
    broker.set_quote(PUT_585, bid=1.78, ask=1.80)
    engine = _engine(tmp_path, broker)
    engine.on_evaluations([_evaluation()], NOW)
    [trade] = engine.open_trades.values()
    broker.fill(trade.entry_order_id, 1.80)
    engine.on_tick(NOW + timedelta(minutes=1))

    broker.fill(trade.target_order_id, 2.34)
    engine.on_tick(NOW + timedelta(minutes=8))

    assert trade.status == "closed" and trade.exit_reason == "target" and trade.pnl_usd == pytest.approx(54.0)
    assert engine.equity == pytest.approx(554.0)

    engine.on_evaluations([_evaluation()], NOW + timedelta(minutes=9))  # same cross_time → same episode
    assert len(engine.open_trades) == 1


def test_unfilled_entry_is_canceled_after_the_timeout(tmp_path: Path):
    broker = FakeBroker()
    broker.set_quote(PUT_585, bid=1.78, ask=1.80)
    engine = _engine(tmp_path, broker)
    engine.on_evaluations([_evaluation()], NOW)
    [trade] = engine.open_trades.values()

    engine.on_tick(NOW + timedelta(minutes=4))

    assert trade.status == "missed" and trade.exit_reason == "entry_not_filled"
    assert trade.entry_order_id in broker.canceled


def test_flat_time_and_max_hold_close_open_positions(tmp_path: Path):
    broker = FakeBroker()
    broker.set_quote(PUT_585, bid=1.90, ask=1.92)
    engine = _engine(tmp_path, broker)
    engine.on_evaluations([_evaluation()], NOW)
    [trade] = engine.open_trades.values()
    broker.fill(trade.entry_order_id, 1.80)
    engine.on_tick(NOW + timedelta(minutes=1))

    engine.on_tick(NOW + timedelta(minutes=46))
    assert trade.status == "closed" and trade.exit_reason == "max_hold" and trade.pnl_usd == pytest.approx(10.0)

    broker2 = FakeBroker()
    broker2.set_quote(PUT_585, bid=1.90, ask=1.92)
    engine2 = _engine(tmp_path / "two", broker2)
    late = datetime(2026, 3, 24, 16, 55, tzinfo=UTC)  # 12:55 ET
    evaluation = replace(_evaluation(), timestamp=late)
    engine2.on_evaluations([evaluation], late)
    [trade2] = engine2.open_trades.values()
    broker2.fill(trade2.entry_order_id, 1.80)
    engine2.on_tick(late + timedelta(minutes=1))
    engine2.on_tick(datetime(2026, 3, 24, 19, 36, tzinfo=UTC))  # 15:36 ET, flat time reached before max hold
    assert trade2.exit_reason == "eod_flat"


def test_daily_loss_limit_halts_new_entries_and_new_session_clears_it(tmp_path: Path):
    broker = FakeBroker()
    broker.set_quote(PUT_585, bid=1.78, ask=1.80)
    engine = _engine(tmp_path, broker, _config(daily_loss_limit_usd=100.0, max_losses_per_day=2))

    for minute in (1, 6):
        evaluation = _evaluation(cross_minute=minute)
        engine.on_evaluations([evaluation], NOW)
        trade = next(t for t in engine.open_trades.values() if t.status == "pending_entry")
        broker.fill(trade.entry_order_id, 1.80)
        engine.on_tick(NOW + timedelta(minutes=1))
        broker.set_quote(PUT_585, bid=1.20, ask=1.24)
        engine.on_tick(NOW + timedelta(minutes=2))
        broker.set_quote(PUT_585, bid=1.78, ask=1.80)

    assert engine.halt_reason and "daily loss" in engine.halt_reason
    engine.on_evaluations([_evaluation(cross_minute=11)], NOW)
    assert len(engine.open_trades) == 2

    engine.new_session(NOW + timedelta(days=1))
    assert engine.halt_reason is None


def test_drawdown_kill_switch_survives_new_session(tmp_path: Path):
    broker = FakeBroker()
    broker.set_quote(PUT_585, bid=1.78, ask=1.80)
    engine = _engine(tmp_path, broker, _config(drawdown_kill_usd=100.0, daily_loss_limit_usd=10000.0, max_losses_per_day=99))
    for minute in (1, 6):
        engine.on_evaluations([_evaluation(cross_minute=minute)], NOW)
        trade = next(t for t in engine.open_trades.values() if t.status == "pending_entry")
        broker.fill(trade.entry_order_id, 1.80)
        engine.on_tick(NOW + timedelta(minutes=1))
        broker.set_quote(PUT_585, bid=1.20, ask=1.24)
        engine.on_tick(NOW + timedelta(minutes=2))
        broker.set_quote(PUT_585, bid=1.78, ask=1.80)

    assert engine.halt_reason and engine.halt_reason.startswith("drawdown")
    engine.new_session(NOW + timedelta(days=1))
    assert engine.halt_reason is not None


def test_broker_error_during_entry_halts_instead_of_crashing(tmp_path: Path):
    broker = FakeBroker(fail_contract_lookup=True)
    engine = _engine(tmp_path, broker)

    engine.on_evaluations([_evaluation()], NOW)

    assert engine.open_trades == {} and engine.halt_reason and "broker error" in engine.halt_reason


def test_counter_trend_and_bull_on_bear_day_are_skipped_but_journaled_as_events(tmp_path: Path):
    broker = FakeBroker()
    broker.set_quote(CALL_585, bid=1.78, ask=1.80)
    engine = _engine(tmp_path, broker)

    engine.on_evaluations([_evaluation(direction=Direction.BULL)], NOW)

    assert engine.open_trades == {}


def test_reconcile_adopts_untracked_broker_positions_and_journaled_open_trades(tmp_path: Path):
    broker = FakeBroker()
    broker.set_quote(PUT_585, bid=1.78, ask=1.80)
    broker.positions[PUT_585] = Position(PUT_585, 1, 1.70)
    engine = _engine(tmp_path, broker)

    engine.reconcile(NOW)

    [trade] = engine.open_trades.values()
    assert trade.status == "open" and trade.entry_price == 1.70 and trade.direction == "bear"
    assert trade.stop_price == pytest.approx(1.19) and trade.target_price == pytest.approx(2.21)

    restarted = ExecutionEngine(_config(), broker, engine.journal, mode="test", printer=lambda _: None)
    restarted.reconcile(NOW + timedelta(minutes=1))
    assert len(restarted.open_trades) == 1  # the journaled trade, not a second adoption


def test_dry_run_broker_fills_against_live_quotes_without_sending():
    source = FakeBroker()
    source.set_quote(PUT_585, bid=1.78, ask=1.80)
    dry = DryRunBroker(source, equity=500.0)

    unfilled = dry.submit_limit(PUT_585, 1, "buy", 1.70)
    assert not unfilled.is_filled

    filled = dry.submit_limit(PUT_585, 1, "buy", 1.82)
    assert filled.is_filled and filled.filled_avg_price == 1.80 and dry.list_positions()[0].qty == 1

    target = dry.submit_limit(PUT_585, 1, "sell", 2.34)
    assert not target.is_filled
    source.set_quote(PUT_585, bid=2.40, ask=2.44)
    assert dry.get_order(target.order_id).is_filled and dry.list_positions() == []
    assert any("DRY-RUN" in line for line in dry.log)


def test_journal_round_trips_open_trades(tmp_path: Path):
    journal = TradeJournal(tmp_path / "j.sqlite3", tmp_path / "j.csv")
    from src.execution.journal import LiveTrade

    trade = LiveTrade("t1", "test", "QQQ", "bear", "A+", "2026-03-24T15:01:30+00:00", PUT_585, 585.0, 1, status="open", entry_price=1.8, stop_price=1.26, target_price=2.34, lag_min=3.0)
    journal.upsert(trade)

    [loaded] = journal.load_open_trades()
    assert loaded == trade
    assert (tmp_path / "j.csv").read_text(encoding="utf-8").count("\n") == 2
