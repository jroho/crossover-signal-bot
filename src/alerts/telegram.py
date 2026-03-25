from __future__ import annotations

import requests

from src.config import AppConfig
from src.models import AlertPayload, Direction, SetupEvaluation


def format_alert(evaluation: SetupEvaluation) -> AlertPayload:
    cross_detail = evaluation.sma_cross_signal
    if evaluation.sma_cross_status:
        cross_detail = f"{cross_detail} ({evaluation.sma_cross_status})"
    message = "\n".join(
        [
            f"{evaluation.symbol} {evaluation.timeframe.value} {evaluation.direction.value.upper()} ALERT",
            f"Price: {evaluation.last_price:.2f}",
            f"Grade: {evaluation.grade.value}",
            f"VWAP: {evaluation.vwap_relation}",
            f"EMA 9: {evaluation.ema9_relation}",
            f"15/30 Cross: {cross_detail}",
            f"15/30 Trend: {evaluation.sma_trend_relation}",
            f"15 SMA slope: {evaluation.sma15_slope if evaluation.sma15_slope is not None else 'unknown'}",
            f"30 SMA slope: {evaluation.sma30_slope if evaluation.sma30_slope is not None else 'unknown'}",
            f"RVGI Sign: {evaluation.rvgi_sign}",
            f"RVGI vs RVGI SMA: {evaluation.rvgi_vs_sma}",
            f"Volume: {evaluation.volume_grade}",
            f"1m agreement: {evaluation.one_min_agreement}",
            f"Strike bias: {evaluation.strike_bias.value}",
            f"Reason: {evaluation.rationale}",
            f"Passed: {', '.join(evaluation.passed_conditions) if evaluation.passed_conditions else 'none'}",
            f"Weak/Failed: {', '.join(evaluation.weak_conditions + evaluation.failed_conditions) if (evaluation.weak_conditions or evaluation.failed_conditions) else 'none'}",
        ]
    )
    return AlertPayload(
        symbol=evaluation.symbol,
        timestamp=evaluation.timestamp,
        timeframe=evaluation.timeframe,
        direction=evaluation.direction,
        grade=evaluation.grade,
        strike_bias=evaluation.strike_bias,
        title=f"{evaluation.symbol} {evaluation.timeframe.value} {evaluation.direction.value.upper()} ALERT",
        message=message,
    )


class TelegramAlerter:
    def __init__(self, config: AppConfig, session: requests.Session | None = None) -> None:
        self.config = config
        self.session = session or requests.Session()

    def send(self, payload: AlertPayload) -> tuple[bool, str]:
        if not self.config.telegram.enabled:
            return False, "Telegram transport is disabled."
        if not self.config.telegram.bot_token or not self.config.telegram.chat_id:
            return False, "Telegram credentials are missing."

        url = f"https://api.telegram.org/bot{self.config.telegram.bot_token}/sendMessage"
        response = self.session.post(
            url,
            json={
                "chat_id": self.config.telegram.chat_id,
                "text": payload.message,
            },
            timeout=15,
        )
        response.raise_for_status()
        return True, "sent"
