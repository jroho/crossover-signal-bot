from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from src.backtest.context import DayContext
from src.backtest.strikes import select_strikes
from src.config import AppConfig
from src.grading import grade_is_alertable
from src.models import Direction, SetupEvaluation

from .broker import Broker
from .journal import LiveTrade, TradeJournal
from .policy import TradingPolicy


class ExecutionEngine:
    """Turns graded evaluations into one option trade per crossover episode and manages it to exit.

    Every action is journaled. New entries stop on the daily loss cap, the drawdown kill switch, or a broker
    error; open positions are still managed to exit in those states.
    """

    def __init__(
        self,
        config: AppConfig,
        broker: Broker,
        journal: TradeJournal,
        *,
        mode: str = "paper",
        printer: Callable[[str], None] = print,
    ) -> None:
        self.config = config
        self.trading = config.trading
        self.broker = broker
        self.journal = journal
        self.mode = mode
        self.printer = printer
        self.policy = TradingPolicy(config)
        self.market_timezone = ZoneInfo(config.app.market_timezone)
        self.context: dict[str, DayContext] = {}
        self.open_trades: dict[str, LiveTrade] = {}
        self.seen_episodes: set[tuple[str, str, str]] = set()
        self.realized_today: float = 0.0
        self.equity_high: float = self.trading.risk_capital_usd
        self.equity: float = self.trading.risk_capital_usd
        self.halt_reason: str | None = None
        self.losses_today: int = 0

    # ------------------------------------------------------------------ setup

    def set_context(self, symbol: str, context: DayContext, now: datetime) -> None:
        self.context[symbol.upper()] = context
        self._log(now, "info", f"{symbol} context: bias={context.trend_label} vix={context.vix_regime} ({context.vix_prev_close})")

    def reconcile(self, now: datetime) -> None:
        """Adopt journal and broker state after a restart so exits are still managed."""
        for trade in self.journal.load_open_trades():
            self.open_trades[trade.trade_id] = trade
            self.seen_episodes.add((trade.symbol, trade.direction, trade.cross_time))
        journaled = {trade.occ_symbol for trade in self.open_trades.values()}
        for position in self.broker.list_positions():
            if position.occ_symbol in journaled:
                continue
            trade = LiveTrade(
                trade_id=f"adopted-{uuid.uuid4().hex[:8]}",
                mode=self.mode,
                symbol=position.occ_symbol[:3],
                direction="bull" if "C" in position.occ_symbol[-9:-8] else "bear",
                grade="?",
                cross_time="unknown",
                occ_symbol=position.occ_symbol,
                strike=int(position.occ_symbol[-8:]) / 1000.0,
                contracts=position.qty,
                status="open",
                opened_at=now.isoformat(),
                entry_price=position.avg_entry_price,
                filled_at=now.isoformat(),
                stop_price=self.policy.stop_price(position.avg_entry_price),
                target_price=self.policy.target_price(position.avg_entry_price),
                notes="adopted from broker positions on startup",
            )
            self.open_trades[trade.trade_id] = trade
            self.journal.upsert(trade)
            self._log(now, "warn", f"adopted untracked position {position.occ_symbol} x{position.qty}")

    # ------------------------------------------------------------------ entries

    def on_evaluations(self, evaluations: list[SetupEvaluation], now: datetime) -> None:
        for evaluation in evaluations:
            self._consider(evaluation, now)

    def _consider(self, evaluation: SetupEvaluation, now: datetime) -> None:
        if not grade_is_alertable(evaluation.grade, self.config.grading.alert_grades):
            return
        episode = (evaluation.symbol, evaluation.direction.value, evaluation.sma_cross_time.isoformat() if evaluation.sma_cross_time else "none")
        if episode in self.seen_episodes:
            return
        if self.halt_reason:
            self.seen_episodes.add(episode)
            self._log(now, "info", f"skip {evaluation.symbol} {evaluation.direction.value}: halted ({self.halt_reason})")
            return
        if len([t for t in self.open_trades.values() if t.is_open]) >= self.trading.max_open_positions:
            self._log(now, "info", f"skip {evaluation.symbol} {evaluation.direction.value}: max open positions")
            return
        context = self.context.get(evaluation.symbol.upper(), DayContext())
        today = now.astimezone(self.market_timezone).date()
        choice = next(item for item in select_strikes(evaluation.symbol, today, evaluation.direction, evaluation.last_price) if item.label == "ATM")
        try:
            contract = self.broker.find_contract(evaluation.symbol, today, choice.option_type, choice.strike)
            quote = self.broker.latest_quote(contract.occ_symbol) if contract else None
        except Exception as exc:  # noqa: BLE001 - broker trouble means no new risk, not a crash
            self._halt(now, f"broker error during entry: {type(exc).__name__}: {exc}")
            return
        if contract is None or quote is None or quote.ask <= 0:
            self.seen_episodes.add(episode)
            self._log(now, "warn", f"skip {evaluation.symbol} {evaluation.direction.value}: no contract/quote for {choice.occ_symbol}")
            return
        decision = self.policy.evaluate_entry(evaluation, context, now, quote.ask)
        self.seen_episodes.add(episode)
        if not decision.allowed:
            self._log(now, "info", f"skip {evaluation.symbol} {evaluation.direction.value} {evaluation.grade.value}: {decision.reason}")
            return
        limit = round(quote.ask + self.trading.entry_limit_buffer, 2)
        order = self.broker.submit_limit(contract.occ_symbol, decision.contracts, "buy", limit)
        trade = LiveTrade(
            trade_id=f"{self.mode}-{uuid.uuid4().hex[:8]}",
            mode=self.mode,
            symbol=evaluation.symbol,
            direction=evaluation.direction.value,
            grade=evaluation.grade.value,
            cross_time=episode[2],
            occ_symbol=contract.occ_symbol,
            strike=contract.strike,
            contracts=decision.contracts,
            opened_at=now.isoformat(),
            entry_order_id=order.order_id,
            entry_limit=limit,
            decision=decision.reason,
            trend_label=context.trend_label,
            vix_regime=context.vix_regime,
            lag_min=evaluation.sma_cross_lag_min,
        )
        self.open_trades[trade.trade_id] = trade
        self.journal.upsert(trade)
        self._log(now, "trade", f"ENTRY {trade.symbol} {trade.direction} {trade.grade} {contract.occ_symbol} x{decision.contracts} limit {limit:.2f} ({decision.reason})")
        if order.is_filled:
            self._on_entry_filled(trade, order.filled_avg_price or limit, now)

    # ------------------------------------------------------------------ management

    def on_tick(self, now: datetime) -> None:
        for trade in list(self.open_trades.values()):
            if not trade.is_open:
                continue
            try:
                if trade.status == "pending_entry":
                    self._manage_pending_entry(trade, now)
                else:
                    self._manage_open(trade, now)
            except Exception as exc:  # noqa: BLE001
                self._log(now, "error", f"{trade.trade_id}: {type(exc).__name__}: {exc}")

    def _manage_pending_entry(self, trade: LiveTrade, now: datetime) -> None:
        order = self.broker.get_order(trade.entry_order_id or "")
        if order.is_filled:
            self._on_entry_filled(trade, order.filled_avg_price or trade.entry_limit or 0.0, now)
            return
        opened = datetime.fromisoformat(trade.opened_at or now.isoformat())
        if now - opened >= timedelta(minutes=self.trading.entry_timeout_minutes) or not order.is_open:
            if order.is_open:
                self.broker.cancel_order(order.order_id)
            trade.status = "missed"
            trade.exit_reason = "entry_not_filled"
            trade.closed_at = now.isoformat()
            self.journal.upsert(trade)
            self._log(now, "trade", f"MISSED {trade.occ_symbol}: entry not filled within {self.trading.entry_timeout_minutes} min")

    def _on_entry_filled(self, trade: LiveTrade, fill_price: float, now: datetime) -> None:
        trade.status = "open"
        trade.entry_price = fill_price
        trade.filled_at = now.isoformat()
        trade.target_price = self.policy.target_price(fill_price)
        trade.stop_price = self.policy.stop_price(fill_price)
        target = self.broker.submit_limit(trade.occ_symbol, trade.contracts, "sell", trade.target_price)
        trade.target_order_id = target.order_id
        self.journal.upsert(trade)
        self._log(now, "trade", f"FILLED {trade.occ_symbol} x{trade.contracts} @ {fill_price:.2f}; target {trade.target_price:.2f} stop {trade.stop_price:.2f}")

    def _manage_open(self, trade: LiveTrade, now: datetime) -> None:
        if trade.target_order_id:
            target = self.broker.get_order(trade.target_order_id)
            if target.is_filled:
                self._close_trade(trade, target.filled_avg_price or trade.target_price or 0.0, "target", now)
                return
        filled_at = datetime.fromisoformat(trade.filled_at or now.isoformat())
        quote = self.broker.latest_quote(trade.occ_symbol)
        mark = quote.bid if quote else None
        reason = None
        if mark is not None and trade.stop_price is not None and mark <= trade.stop_price:
            reason = "stop"
        elif self.policy.past_flat_time(now):
            reason = "eod_flat"
        elif now - filled_at >= timedelta(minutes=self.trading.max_hold_minutes):
            reason = "max_hold"
        if reason is None:
            return
        if trade.target_order_id:
            target = self.broker.get_order(trade.target_order_id)
            if target.is_open:
                self.broker.cancel_order(trade.target_order_id)
        exit_order = self.broker.close_position(trade.occ_symbol)
        exit_price = (exit_order.filled_avg_price if exit_order and exit_order.filled_avg_price else mark) or (trade.entry_price or 0.0)
        trade.exit_order_id = exit_order.order_id if exit_order else None
        self._close_trade(trade, exit_price, reason, now)

    def _close_trade(self, trade: LiveTrade, exit_price: float, reason: str, now: datetime) -> None:
        trade.status = "closed"
        trade.exit_price = exit_price
        trade.exit_reason = reason
        trade.closed_at = now.isoformat()
        trade.pnl_usd = round((exit_price - (trade.entry_price or exit_price)) * 100.0 * trade.contracts, 2)
        self.journal.upsert(trade)
        self.realized_today += trade.pnl_usd
        self.equity += trade.pnl_usd
        self.equity_high = max(self.equity_high, self.equity)
        if trade.pnl_usd < 0:
            self.losses_today += 1
        self._log(now, "trade", f"EXIT {trade.occ_symbol} {reason} @ {exit_price:.2f} pnl {trade.pnl_usd:+.2f} (day {self.realized_today:+.2f})")
        self._check_risk(now)

    # ------------------------------------------------------------------ risk

    def _check_risk(self, now: datetime) -> None:
        if self.realized_today <= -abs(self.trading.daily_loss_limit_usd) or self.losses_today >= self.trading.max_losses_per_day:
            self._halt(now, f"daily loss limit (realized {self.realized_today:+.2f}, losses {self.losses_today})")
        elif self.equity_high - self.equity >= abs(self.trading.drawdown_kill_usd):
            self._halt(now, f"drawdown kill switch ({self.equity_high - self.equity:.2f} from high)")

    def _halt(self, now: datetime, reason: str) -> None:
        if self.halt_reason is None:
            self.halt_reason = reason
            self._log(now, "warn", f"HALT new entries: {reason}")

    def new_session(self, now: datetime) -> None:
        self.realized_today = 0.0
        self.losses_today = 0
        self.seen_episodes.clear()
        if self.halt_reason and not self.halt_reason.startswith("drawdown"):
            self.halt_reason = None
        self._log(now, "info", "new session")

    def _log(self, now: datetime, level: str, message: str) -> None:
        self.journal.event(now, level, message)
        self.printer(f"[{now.astimezone(self.market_timezone):%H:%M:%S}] {message}")
