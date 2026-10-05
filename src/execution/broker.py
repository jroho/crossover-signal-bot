from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any, Protocol

from src.config import AppConfig


@dataclass(frozen=True)
class Contract:
    occ_symbol: str
    underlying: str
    expiration: date
    option_type: str
    strike: float


@dataclass(frozen=True)
class Quote:
    occ_symbol: str
    bid: float
    ask: float
    timestamp: datetime

    @property
    def mid(self) -> float:
        return round((self.bid + self.ask) / 2.0, 4)


@dataclass(frozen=True)
class OrderInfo:
    order_id: str
    occ_symbol: str
    side: str
    qty: int
    status: str
    limit_price: float | None
    filled_qty: int = 0
    filled_avg_price: float | None = None

    @property
    def is_filled(self) -> bool:
        return self.status == "filled"

    @property
    def is_open(self) -> bool:
        return self.status in {"new", "accepted", "pending_new", "partially_filled", "pending_replace", "pending_cancel"}


@dataclass(frozen=True)
class Position:
    occ_symbol: str
    qty: int
    avg_entry_price: float


@dataclass(frozen=True)
class AccountSnapshot:
    status: str
    equity: float
    buying_power: float
    options_level: int | None
    multiplier: int | None
    crypto_status: str | None


class Broker(Protocol):
    def account_equity(self) -> float: ...
    def buying_power(self) -> float: ...
    def account_snapshot(self) -> AccountSnapshot: ...
    def is_market_open(self) -> bool: ...
    def find_contract(self, underlying: str, expiration: date, option_type: str, strike: float) -> Contract | None: ...
    def latest_quote(self, occ_symbol: str) -> Quote | None: ...
    def submit_limit(self, occ_symbol: str, qty: int, side: str, limit_price: float) -> OrderInfo: ...
    def get_order(self, order_id: str) -> OrderInfo: ...
    def cancel_order(self, order_id: str) -> None: ...
    def close_position(self, occ_symbol: str) -> OrderInfo | None: ...
    def list_positions(self) -> list[Position]: ...


class AlpacaBroker:
    """Thin wrapper over alpaca-py: paper or live is decided by the config flag, nothing else differs."""

    def __init__(self, config: AppConfig, trading_client: Any | None = None, option_data_client: Any | None = None) -> None:
        self.config = config
        self._trading = trading_client
        self._option_data = option_data_client

    @property
    def trading(self) -> Any:
        if self._trading is None:
            from alpaca.trading.client import TradingClient

            self._trading = TradingClient(
                api_key=self.config.alpaca.api_key_id,
                secret_key=self.config.alpaca.api_secret_key,
                paper=self.config.trading.paper,
            )
        return self._trading

    @property
    def option_data(self) -> Any:
        if self._option_data is None:
            from alpaca.data.historical import OptionHistoricalDataClient

            self._option_data = OptionHistoricalDataClient(
                api_key=self.config.alpaca.api_key_id,
                secret_key=self.config.alpaca.api_secret_key,
            )
        return self._option_data

    def account_equity(self) -> float:
        return float(self.trading.get_account().equity)

    def buying_power(self) -> float:
        account = self.trading.get_account()
        value = getattr(account, "options_buying_power", None) or account.buying_power
        return float(value)

    def account_snapshot(self) -> AccountSnapshot:
        account = self.trading.get_account()
        status = account.status.value if hasattr(account.status, "value") else str(account.status)
        crypto = getattr(account, "crypto_status", None)
        return AccountSnapshot(
            status=status,
            equity=float(account.equity),
            buying_power=float(getattr(account, "options_buying_power", None) or account.buying_power),
            options_level=int(account.options_trading_level) if getattr(account, "options_trading_level", None) is not None else None,
            multiplier=int(float(account.multiplier)) if getattr(account, "multiplier", None) is not None else None,
            crypto_status=crypto.value if hasattr(crypto, "value") else (str(crypto) if crypto is not None else None),
        )

    def is_market_open(self) -> bool:
        return bool(self.trading.get_clock().is_open)

    def find_contract(self, underlying: str, expiration: date, option_type: str, strike: float) -> Contract | None:
        from alpaca.trading.enums import AssetStatus, ContractType
        from alpaca.trading.requests import GetOptionContractsRequest

        request = GetOptionContractsRequest(
            underlying_symbols=[underlying.upper()],
            expiration_date=expiration,
            type=ContractType.CALL if option_type == "call" else ContractType.PUT,
            strike_price_gte=f"{strike - 0.005:.3f}",
            strike_price_lte=f"{strike + 0.005:.3f}",
            status=AssetStatus.ACTIVE,
            limit=5,
        )
        response = self.trading.get_option_contracts(request)
        contracts = getattr(response, "option_contracts", None) or []
        for item in contracts:
            if getattr(item, "tradable", True):
                return Contract(
                    occ_symbol=item.symbol,
                    underlying=underlying.upper(),
                    expiration=expiration,
                    option_type=option_type,
                    strike=float(item.strike_price),
                )
        return None

    def latest_quote(self, occ_symbol: str) -> Quote | None:
        from alpaca.data.enums import OptionsFeed
        from alpaca.data.requests import OptionLatestQuoteRequest

        feed = OptionsFeed.OPRA if self.config.trading.option_feed == "opra" else OptionsFeed.INDICATIVE
        quotes = self.option_data.get_option_latest_quote(OptionLatestQuoteRequest(symbol_or_symbols=[occ_symbol], feed=feed))
        raw = quotes.get(occ_symbol) if isinstance(quotes, dict) else None
        if raw is None:
            return None
        return Quote(
            occ_symbol=occ_symbol,
            bid=float(raw.bid_price),
            ask=float(raw.ask_price),
            timestamp=raw.timestamp if raw.timestamp.tzinfo else raw.timestamp.replace(tzinfo=UTC),
        )

    def submit_limit(self, occ_symbol: str, qty: int, side: str, limit_price: float) -> OrderInfo:
        from alpaca.trading.enums import OrderSide, TimeInForce
        from alpaca.trading.requests import LimitOrderRequest

        order = self.trading.submit_order(
            LimitOrderRequest(
                symbol=occ_symbol,
                qty=qty,
                side=OrderSide.BUY if side == "buy" else OrderSide.SELL,
                time_in_force=TimeInForce.DAY,
                limit_price=round(limit_price, 2),
            )
        )
        return _order_info(order, occ_symbol, side, qty, round(limit_price, 2))

    def get_order(self, order_id: str) -> OrderInfo:
        order = self.trading.get_order_by_id(order_id)
        return _order_info(order, str(order.symbol), str(order.side.value if hasattr(order.side, "value") else order.side), int(float(order.qty or 0)), float(order.limit_price) if order.limit_price else None)

    def cancel_order(self, order_id: str) -> None:
        self.trading.cancel_order_by_id(order_id)

    def close_position(self, occ_symbol: str) -> OrderInfo | None:
        order = self.trading.close_position(occ_symbol)
        if order is None:
            return None
        return _order_info(order, occ_symbol, "sell", int(float(order.qty or 0)), None)

    def list_positions(self) -> list[Position]:
        positions = []
        for item in self.trading.get_all_positions():
            if str(getattr(item, "asset_class", "")).endswith("option") or len(str(item.symbol)) > 12:
                positions.append(Position(occ_symbol=str(item.symbol), qty=int(float(item.qty)), avg_entry_price=float(item.avg_entry_price)))
        return positions


def _order_info(order: Any, occ_symbol: str, side: str, qty: int, limit_price: float | None) -> OrderInfo:
    status = order.status.value if hasattr(order.status, "value") else str(order.status)
    filled_qty = int(float(order.filled_qty or 0))
    filled_avg = float(order.filled_avg_price) if order.filled_avg_price else None
    return OrderInfo(str(order.id), occ_symbol, side, qty, status, limit_price, filled_qty, filled_avg)


@dataclass
class DryRunBroker:
    """Uses a real broker for contracts and quotes, sends nothing, and fills orders itself at the quote.

    Buys fill at the ask when the limit allows, sells at the bid; the first safe mode to run live data through.
    """

    quotes: Broker
    equity: float = 500.0
    orders: dict[str, OrderInfo] = field(default_factory=dict)
    positions: dict[str, Position] = field(default_factory=dict)
    log: list[str] = field(default_factory=list)

    def account_equity(self) -> float:
        return self.equity

    def buying_power(self) -> float:
        return self.equity

    def account_snapshot(self) -> AccountSnapshot:
        real = self.quotes.account_snapshot()
        return AccountSnapshot(real.status, self.equity, self.equity, real.options_level, real.multiplier, real.crypto_status)

    def is_market_open(self) -> bool:
        return self.quotes.is_market_open()

    def find_contract(self, underlying: str, expiration: date, option_type: str, strike: float) -> Contract | None:
        return self.quotes.find_contract(underlying, expiration, option_type, strike)

    def latest_quote(self, occ_symbol: str) -> Quote | None:
        return self.quotes.latest_quote(occ_symbol)

    def submit_limit(self, occ_symbol: str, qty: int, side: str, limit_price: float) -> OrderInfo:
        order_id = f"dry-{uuid.uuid4().hex[:8]}"
        order = OrderInfo(order_id, occ_symbol, side, qty, "accepted", round(limit_price, 2))
        self.orders[order_id] = order
        self.log.append(f"DRY-RUN submit {side} {qty} {occ_symbol} @ {limit_price:.2f}")
        return self._try_fill(order)

    def get_order(self, order_id: str) -> OrderInfo:
        order = self.orders[order_id]
        return self._try_fill(order) if order.is_open else order

    def cancel_order(self, order_id: str) -> None:
        order = self.orders[order_id]
        if order.is_open:
            self.orders[order_id] = OrderInfo(order.order_id, order.occ_symbol, order.side, order.qty, "canceled", order.limit_price)

    def close_position(self, occ_symbol: str) -> OrderInfo | None:
        position = self.positions.pop(occ_symbol, None)
        if position is None:
            return None
        quote = self.quotes.latest_quote(occ_symbol)
        price = quote.bid if quote else position.avg_entry_price
        self.log.append(f"DRY-RUN close {position.qty} {occ_symbol} @ {price:.2f}")
        return OrderInfo(f"dry-{uuid.uuid4().hex[:8]}", occ_symbol, "sell", position.qty, "filled", None, position.qty, price)

    def list_positions(self) -> list[Position]:
        return list(self.positions.values())

    def _try_fill(self, order: OrderInfo) -> OrderInfo:
        quote = self.quotes.latest_quote(order.occ_symbol)
        if quote is None or order.limit_price is None:
            return order
        if order.side == "buy" and quote.ask <= order.limit_price:
            filled = OrderInfo(order.order_id, order.occ_symbol, "buy", order.qty, "filled", order.limit_price, order.qty, quote.ask)
            self.positions[order.occ_symbol] = Position(order.occ_symbol, order.qty, quote.ask)
        elif order.side == "sell" and quote.bid >= order.limit_price:
            filled = OrderInfo(order.order_id, order.occ_symbol, "sell", order.qty, "filled", order.limit_price, order.qty, quote.bid)
            self.positions.pop(order.occ_symbol, None)
        else:
            return order
        self.orders[order.order_id] = filled
        self.log.append(f"DRY-RUN filled {filled.side} {filled.qty} {filled.occ_symbol} @ {filled.filled_avg_price:.2f}")
        return filled
