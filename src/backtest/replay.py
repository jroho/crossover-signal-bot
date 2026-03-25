from __future__ import annotations

from pathlib import Path
from zoneinfo import ZoneInfo

from src.alerts import TelegramAlerter, format_alert
from src.config import AppConfig
from src.data import CsvReplayAdapter
from src.market_hours import is_within_market_hours, parse_clock_time
from src.models import AlertRecord, ReplayResult, SetupEvaluation
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
        self.market_timezone = ZoneInfo(config.app.market_timezone)
        self.market_open = parse_clock_time(config.live.market_open_time, field_name="live.market_open_time")
        self.market_close = parse_clock_time(config.live.market_close_time, field_name="live.market_close_time")

    def run(
        self,
        csv_path: str | None = None,
        export_path: str | None = None,
    ) -> ReplayResult:
        configured_source = csv_path or self.config.replay.csv_path
        source_path = self._resolve_source_path(configured_source)
        candles = self.adapter.load_candles(source_path, self.config.app.symbols)
        evaluations, _, _ = evaluate_symbol(candles, self.config)

        alerts: list[AlertRecord] = []
        for evaluation in evaluations:
            should_alert = evaluation.grade.value in set(self.config.grading.alert_grades)
            if not self._should_emit_alert(evaluation):
                continue
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
        run_id = self.logger.create_run(mode="replay", config=self.config, source=str(source_path))
        self.logger.log_evaluations(run_id, evaluations)
        if alerts:
            self.logger.log_alerts(run_id, alerts)

        if export_path:
            export_evaluations_to_csv(evaluations, export_path, market_timezone=self.config.app.market_timezone)
        elif self.config.replay.export_csv_path:
            export_evaluations_to_csv(
                evaluations,
                self.config.replay.export_csv_path,
                market_timezone=self.config.app.market_timezone,
            )

        if self.config.storage.alert_csv_path and alerts:
            export_alerts_to_csv(alerts, self.config.storage.alert_csv_path, market_timezone=self.config.app.market_timezone)
        if self.config.storage.evaluation_csv_path:
            export_evaluations_to_csv(
                evaluations,
                self.config.storage.evaluation_csv_path,
                market_timezone=self.config.app.market_timezone,
            )

        return ReplayResult(run_id=run_id, evaluations=evaluations, alerts=alerts)

    def _should_emit_alert(self, evaluation: SetupEvaluation) -> bool:
        return is_within_market_hours(
            evaluation.timestamp,
            self.market_timezone,
            self.market_open,
            self.market_close,
        )

    @staticmethod
    def _resolve_source_path(path: str | Path) -> Path:
        candidate = Path(path)
        if candidate.exists():
            return candidate

        alternate_name = candidate.name.replace("_5minute_", "_1minute_", 1)
        if alternate_name != candidate.name:
            alternate = candidate.with_name(alternate_name)
            if alternate.exists():
                return alternate

        return candidate
