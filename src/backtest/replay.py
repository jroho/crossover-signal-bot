from __future__ import annotations

from zoneinfo import ZoneInfo

from src.alerts import TelegramAlerter, format_alert
from src.config import AppConfig
from src.data import CsvReplayAdapter
from src.market_hours import is_within_market_hours, parse_clock_time
from src.models import AlertRecord, Candle, ReplayResult
from src.signals import evaluate_symbol
from src.storage import SQLiteLogger, export_alerts_to_csv, export_evaluations_to_csv


class ReplayEngine:
    def __init__(
        self,
        config: AppConfig,
        adapter: CsvReplayAdapter | None = None,
        logger: SQLiteLogger | None = None,
        alerter: TelegramAlerter | None = None,
    ) -> None:
        self.config = config
        self.adapter = adapter or CsvReplayAdapter()
        self.logger = logger or SQLiteLogger(config.storage.sqlite_path)
        self.alerter = alerter or TelegramAlerter(config)

    def run(
        self,
        csv_path: str | None = None,
        export_path: str | None = None,
        market_hours_only: bool = False,
    ) -> ReplayResult:
        source_path = csv_path or self.config.replay.csv_path
        candles = self.adapter.load_candles(source_path, self.config.app.symbols)
        if market_hours_only:
            candles = self._filter_market_hours(candles)
        evaluations, _, _ = evaluate_symbol(candles, self.config)

        alerts: list[AlertRecord] = []
        for evaluation in evaluations:
            should_alert = evaluation.grade.value in set(self.config.grading.alert_grades)
            if should_alert and (evaluation.strike_bias.value != "skip" or self.config.grading.allow_grade_c_soft_alerts):
                payload = format_alert(evaluation)
                delivered = False
                transport_message = "replay send disabled"
                if self.config.replay.send_telegram:
                    delivered, transport_message = self.alerter.send(payload)
                evaluation.alert_sent = delivered or self.config.replay.send_telegram
                alerts.append(
                    AlertRecord(
                        evaluation=evaluation,
                        payload=payload,
                        delivered=delivered,
                        transport_message=transport_message,
                    )
                )

        self.logger.initialize()
        run_id = self.logger.create_run(mode="replay", config=self.config, source=source_path)
        self.logger.log_evaluations(run_id, evaluations)
        if alerts:
            self.logger.log_alerts(run_id, alerts)

        if export_path:
            export_evaluations_to_csv(evaluations, export_path)
        elif self.config.replay.export_csv_path:
            export_evaluations_to_csv(evaluations, self.config.replay.export_csv_path)

        if self.config.storage.alert_csv_path and alerts:
            export_alerts_to_csv(alerts, self.config.storage.alert_csv_path)
        if self.config.storage.evaluation_csv_path:
            export_evaluations_to_csv(evaluations, self.config.storage.evaluation_csv_path)

        return ReplayResult(run_id=run_id, evaluations=evaluations, alerts=alerts)

    def _filter_market_hours(self, candles: list[Candle]) -> list[Candle]:
        market_timezone = ZoneInfo(self.config.app.market_timezone)
        market_open = parse_clock_time(self.config.live.market_open_time, field_name="live.market_open_time")
        market_close = parse_clock_time(self.config.live.market_close_time, field_name="live.market_close_time")
        return [
            candle
            for candle in candles
            if is_within_market_hours(candle.timestamp, market_timezone, market_open, market_close)
        ]
