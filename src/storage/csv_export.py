from __future__ import annotations

import csv
from pathlib import Path

from src.models import AlertRecord, SetupEvaluation


def export_evaluations_to_csv(evaluations: list[SetupEvaluation], path: str | Path) -> None:
    rows = [evaluation.to_record() for evaluation in evaluations]
    _write_rows(rows, path)


def export_alerts_to_csv(alerts: list[AlertRecord], path: str | Path) -> None:
    rows = []
    for alert in alerts:
        record = alert.evaluation.to_record()
        record.update(
            {
                "alert_title": alert.payload.title,
                "alert_message": alert.payload.message,
                "delivered": int(alert.delivered),
                "transport_message": alert.transport_message,
            }
        )
        rows.append(record)
    _write_rows(rows, path)


def _write_rows(rows: list[dict[str, object]], path: str | Path) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8", newline="") as handle:
        if not rows:
            handle.write("")
            return
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
