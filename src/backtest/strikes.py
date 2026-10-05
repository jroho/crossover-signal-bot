from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from src.models import Direction

# Ordered from deepest in-the-money to furthest out-of-the-money.
STRIKE_LABELS = ("ITM2", "ITM1", "ATM", "OTM1", "OTM2")


@dataclass(frozen=True)
class StrikeChoice:
    label: str
    strike: float
    option_type: str
    occ_symbol: str


def nearest_strike(spot: float, increment: float = 1.0) -> float:
    return round(round(spot / increment) * increment, 2)


def occ_symbol(underlying: str, expiration: date, option_type: str, strike: float) -> str:
    """OCC symbol without root padding, which is how Alpaca keys option data: QQQ260324C00585000."""
    return f"{underlying.upper()}{expiration.strftime('%y%m%d')}{option_type[0].upper()}{int(round(strike * 1000)):08d}"


def select_strikes(
    underlying: str,
    expiration: date,
    direction: Direction,
    spot: float,
    increment: float = 1.0,
) -> list[StrikeChoice]:
    """Bull setups buy calls and bear setups buy puts; ITM sits below spot for calls and above it for puts."""
    atm = nearest_strike(spot, increment)
    option_type = "call" if direction == Direction.BULL else "put"
    itm_step = -increment if option_type == "call" else increment
    offsets = {"ITM2": 2 * itm_step, "ITM1": itm_step, "ATM": 0.0, "OTM1": -itm_step, "OTM2": -2 * itm_step}
    choices: list[StrikeChoice] = []
    for label in STRIKE_LABELS:
        strike = round(atm + offsets[label], 2)
        choices.append(StrikeChoice(label, strike, option_type, occ_symbol(underlying, expiration, option_type, strike)))
    return choices
