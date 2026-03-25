from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from src.models import AlertRecord, SetupEvaluation

POLYGON_AGGREGATE_FIELDNAMES = ["t", "o", "h", "l", "c", "v", "vw", "n"]


def export_evaluations_to_csv(
    evaluations: list[SetupEvaluation],
    path: str | Path,
    market_timezone: str | None = None,
) -> None:
    rows = [_with_market_time_columns(evaluation.to_record(), market_timezone) for evaluation in evaluations]
    _write_rows(rows, path)


def export_alerts_to_csv(
    alerts: list[AlertRecord],
    path: str | Path,
    market_timezone: str | None = None,
) -> None:
    rows = []
    for alert in alerts:
        record = _with_market_time_columns(alert.evaluation.to_record(), market_timezone)
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


def _with_market_time_columns(row: dict[str, object], market_timezone: str | None) -> dict[str, object]:
    if not market_timezone or "datetime" not in row or not row["datetime"]:
        return dict(row)

    market_dt = datetime.fromisoformat(str(row["datetime"])).astimezone(ZoneInfo(market_timezone))
    enriched: dict[str, object] = {}
    for key, value in row.items():
        enriched[key] = value
        if key == "datetime":
            enriched["datetime_market"] = market_dt.isoformat()
            enriched["market_timezone"] = market_timezone
    return enriched


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
