from __future__ import annotations

import csv
from pathlib import Path

from src.models import AlertRecord, SetupEvaluation

POLYGON_AGGREGATE_FIELDNAMES = ["t", "o", "h", "l", "c", "v", "vw", "n"]


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


def export_polygon_aggregate_rows(rows: list[dict[str, object]], path: str | Path) -> None:
    _write_rows(rows, path, fieldnames=POLYGON_AGGREGATE_FIELDNAMES)


def _write_rows(
    rows: list[dict[str, object]],
    path: str | Path,
    fieldnames: list[str] | None = None,
) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8", newline="") as handle:
        if not rows and not fieldnames:
            handle.write("")
            return
        writer = csv.DictWriter(handle, fieldnames=fieldnames or list(rows[0].keys()))
        writer.writeheader()
        if rows:
            writer.writerows(rows)
