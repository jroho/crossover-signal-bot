from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class Timeframe(str, Enum):
    ONE_MINUTE = "1m"
    FIVE_MINUTE = "5m"


class Direction(str, Enum):
    BULL = "bull"
    BEAR = "bear"


class Grade(str, Enum):
    A = "A"
    B = "B"
    C = "C"


class OutcomeGrade(str, Enum):
    A = "A"
    B = "B"
    C = "C"


class VolumeGrade(str, Enum):
    STRONG = "strong"
    ACCEPTABLE = "acceptable"
    WEAK = "weak"
    INSUFFICIENT = "insufficient"


class StrikeBias(str, Enum):
    TWO_OTM = "+/-2 OTM"
    ONE_OTM = "+/-1 OTM"
    ATM = "ATM"
    ONE_ITM = "+/-1 ITM"
    SKIP = "skip"


class OutcomeResult(str, Enum):
    WIN = "win"
    LOSS = "loss"
    FLAT = "flat"


@dataclass(frozen=True)
class Candle:
    symbol: str
    timeframe: Timeframe
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True)
class IndicatorState:
    symbol: str
    timeframe: Timeframe
    timestamp: datetime
    vwap: float | None
    ema9: float | None
    sma15: float | None
    sma30: float | None
    rvgi: float | None
    rvgi_sma: float | None
    recent_volume_avg: float | None
    rolling_volume_avg: float | None
    volume_grade: VolumeGrade


@dataclass(frozen=True)
class OneMinuteConfirmation:
    status: str
    details: str


@dataclass
class SetupEvaluation:
    symbol: str
    timestamp: datetime
    timeframe: Timeframe
    direction: Direction
    last_price: float
    vwap_relation: str
    ema9_relation: str
    sma15_value: float | None
    sma30_value: float | None
    sma_trend_relation: str
    sma_cross_signal: str
    sma_cross_status: str
    sma_cross_time: datetime | None
    sma15_slope: float | None
    sma30_slope: float | None
    rvgi: float | None
    rvgi_sma: float | None
    rvgi_vs_sma: str
    rvgi_sign: str
    volume: float
    recent_volume_avg: float | None
    rolling_volume_avg: float | None
    volume_grade: str
    one_min_agreement: str
    grade: Grade
    strike_bias: StrikeBias
    strike_bias_reason: str
    passed_conditions: list[str] = field(default_factory=list)
    weak_conditions: list[str] = field(default_factory=list)
    failed_conditions: list[str] = field(default_factory=list)
    rationale: str = ""
    alert_sent: bool = False
    forward_return_3m: float | None = None
    forward_return_5m: float | None = None
    forward_return_10m: float | None = None
    forward_return_15m: float | None = None
    forward_return_30m: float | None = None
    pop_outcome: OutcomeResult | None = None
    pop_outcome_horizon: str | None = None
    pop_grade: OutcomeGrade | None = None

    def to_record(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "datetime": self.timestamp.isoformat(),
            "timeframe": self.timeframe.value,
            "direction": self.direction.value,
            "last_price": self.last_price,
            "vwap_relation": self.vwap_relation,
            "ema9_relation": self.ema9_relation,
            "sma15_value": self.sma15_value,
            "sma30_value": self.sma30_value,
            "sma_trend_relation": self.sma_trend_relation,
            "sma_cross_signal": self.sma_cross_signal,
            "sma_cross_status": self.sma_cross_status,
            "sma_cross_time": self.sma_cross_time.isoformat() if self.sma_cross_time else None,
            "sma15_slope": self.sma15_slope,
            "sma30_slope": self.sma30_slope,
            "rvgi": self.rvgi,
            "rvgi_sma": self.rvgi_sma,
            "rvgi_vs_sma": self.rvgi_vs_sma,
            "rvgi_sign": self.rvgi_sign,
            "volume": self.volume,
            "recent_volume_avg": self.recent_volume_avg,
            "rolling_volume_avg": self.rolling_volume_avg,
            "volume_grade": self.volume_grade,
            "one_min_agreement": self.one_min_agreement,
            "grade": self.grade.value,
            "strike_bias": self.strike_bias.value,
            "strike_bias_reason": self.strike_bias_reason,
            "passed_conditions": "|".join(self.passed_conditions),
            "weak_conditions": "|".join(self.weak_conditions),
            "failed_conditions": "|".join(self.failed_conditions),
            "rationale": self.rationale,
            "alert_sent": int(self.alert_sent),
            "forward_return_3m": self.forward_return_3m,
            "forward_return_5m": self.forward_return_5m,
            "forward_return_10m": self.forward_return_10m,
            "forward_return_15m": self.forward_return_15m,
            "forward_return_30m": self.forward_return_30m,
            "pop_outcome": self.pop_outcome.value if self.pop_outcome else None,
            "pop_outcome_horizon": self.pop_outcome_horizon,
            "pop_grade": self.pop_grade.value if self.pop_grade else None,
        }


@dataclass(frozen=True)
class AlertPayload:
    symbol: str
    timestamp: datetime
    timeframe: Timeframe
    direction: Direction
    grade: Grade
    strike_bias: StrikeBias
    title: str
    message: str


@dataclass(frozen=True)
class AlertRecord:
    evaluation: SetupEvaluation
    payload: AlertPayload
    delivered: bool
    transport_message: str


@dataclass(frozen=True)
class ReplayResult:
    run_id: str
    evaluations: list[SetupEvaluation]
    alerts: list[AlertRecord]
