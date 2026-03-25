from __future__ import annotations

import csv
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from src.models import AlertRecord, SetupEvaluation

POLYGON_AGGREGATE_FIELDNAMES = ["t", "o", "h", "l", "c", "v", "vw", "n"]
REPLAY_CANDLE_FIELDNAMES = ["timestamp", "open", "high", "low", "close", "volume", "symbol"]
DATETIME_EXPORT_FIELDS = {"datetime", "sma_cross_time"}


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


def export_replay_candle_rows(rows: list[dict[str, object]], path: str | Path) -> None:
    _write_rows(rows, path, fieldnames=REPLAY_CANDLE_FIELDNAMES)


def polygon_aggregate_rows_to_replay_rows(
    rows: list[dict[str, object]],
    symbol: str,
) -> list[dict[str, object]]:
    replay_rows: list[dict[str, object]] = []
    normalized_symbol = symbol.upper()
    for row in rows:
        replay_rows.append(
            {
                "timestamp": _format_polygon_timestamp(row.get("t")),
                "open": row.get("o"),
                "high": row.get("h"),
                "low": row.get("l"),
                "close": row.get("c"),
                "volume": row.get("v"),
                "symbol": normalized_symbol,
            }
        )
    return replay_rows


def _with_market_time_columns(row: dict[str, object], market_timezone: str | None) -> dict[str, object]:
    if not market_timezone:
        return dict(row)

    enriched: dict[str, object] = {}
    for key, value in row.items():
        enriched[key] = value
        formatted = _format_market_datetime(value, market_timezone) if key in DATETIME_EXPORT_FIELDS else None
        if formatted is None:
            continue
        enriched[f"{key}_market"] = formatted
        enriched["market_timezone"] = market_timezone
    return enriched


def _format_market_datetime(value: object, market_timezone: str) -> str | None:
    if value in {None, ""}:
        return None
    market_dt = datetime.fromisoformat(str(value)).astimezone(ZoneInfo(market_timezone))
    return market_dt.strftime("%Y-%m-%d %I:%M:%S %p %Z")


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
        writer = csv.DictWriter(handle, fieldnames=fieldnames or _fieldnames_for_rows(rows))
        writer.writeheader()
        if rows:
            writer.writerows(rows)


def _fieldnames_for_rows(rows: list[dict[str, object]]) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row.keys():
            if key in seen:
                continue
            seen.add(key)
            ordered.append(key)
    return ordered


def _format_polygon_timestamp(value: object) -> str:
    if value is None:
        raise ValueError("Polygon aggregate row is missing the 't' timestamp field.")
    timestamp = datetime.fromtimestamp(float(value) / 1000, tz=UTC)
    return timestamp.strftime("%Y-%m-%dT%H:%M:%S.0000000+00:00")


