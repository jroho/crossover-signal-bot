from dataclasses import replace
from datetime import date

from src.data import PolygonAdapter
from src.main import main


class _FakeResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, object]:
        return self.payload


class _RecordingSession:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload
        self.calls: list[dict[str, object]] = []

    def get(self, url: str, params: dict[str, object], timeout: int) -> _FakeResponse:
        self.calls.append({"url": url, "params": params, "timeout": timeout})
        return _FakeResponse(self.payload)


def test_polygon_adapter_fetches_single_day_aggregate_rows(base_config):
    payload = {
        "results": [
            {
                "t": 1774362600000,
                "o": 586.1,
                "h": 586.4,
                "l": 585.9,
                "c": 586.24,
                "v": 12500,
                "vw": 586.18,
                "n": 812,
            }
        ]
    }
    session = _RecordingSession(payload)
    config = replace(base_config, polygon=replace(base_config.polygon, enabled=True, api_key="test-key"))
    adapter = PolygonAdapter(config, session=session)

    rows = adapter.get_single_day_aggregate_rows("qqq", date(2026, 3, 24), 5)

    assert rows == [
        {
            "t": 1774362600000,
            "o": 586.1,
            "h": 586.4,
            "l": 585.9,
            "c": 586.24,
            "v": 12500,
            "vw": 586.18,
            "n": 812,
        }
    ]
    assert session.calls == [
        {
            "url": "https://api.polygon.io/v2/aggs/ticker/QQQ/range/5/minute/2026-03-24/2026-03-24",
            "params": {
                "adjusted": "true",
                "sort": "asc",
                "limit": 50000,
                "apiKey": "test-key",
            },
            "timeout": 30,
        }
    ]


def test_fetch_day_command_writes_replay_compatible_csv(monkeypatch, tmp_path, capsys):
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
[app]
symbols = ["SPY"]

[polygon]
api_key = "test-key"
""".strip(),
        encoding="utf-8",
    )
    output_path = tmp_path / "spy_day.csv"
    captured: dict[str, object] = {}

    class FakeAdapter:
        def __init__(self, config) -> None:
            self.config = config

        def get_single_day_aggregate_rows(self, symbol: str, day: date, multiplier: int) -> list[dict[str, object]]:
            captured["symbol"] = symbol
            captured["day"] = day
            captured["multiplier"] = multiplier
            return [
                {
                    "t": 1774362600000,
                    "o": 586.1,
                    "h": 586.4,
                    "l": 585.9,
                    "c": 586.24,
                    "v": 12500,
                    "vw": 586.18,
                    "n": 812,
                }
            ]

    monkeypatch.setattr("src.main.PolygonAdapter", FakeAdapter)

    main([
        "--config",
        str(config_path),
        "fetch-day",
        "-date",
        "2026-03-24",
        "-multiplier",
        "1",
        "--output",
        str(output_path),
    ])

    assert captured == {
        "symbol": "SPY",
        "day": date(2026, 3, 24),
        "multiplier": 1,
    }
    assert output_path.read_text(encoding="utf-8") == (
        "timestamp,open,high,low,close,volume,symbol\n"
        "2026-03-24T14:30:00.0000000+00:00,586.1,586.4,585.9,586.24,12500,SPY\n"
    )
    assert capsys.readouterr().out.strip() == f"Saved 1 rows to {output_path}"


def test_fetch_day_rejects_non_one_minute_multiplier(tmp_path):
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
[app]
symbols = ["SPY"]

[polygon]
api_key = "test-key"
""".strip(),
        encoding="utf-8",
    )

    try:
        main([
            "--config",
            str(config_path),
            "fetch-day",
            "-date",
            "2026-03-24",
            "-multiplier",
            "5",
        ])
    except SystemExit as exc:
        assert str(exc) == "fetch-day exports replay-compatible candles and currently requires --multiplier 1."
    else:
        raise AssertionError("Expected fetch-day to reject non-1 multipliers.")


def test_init_db_accepts_config_after_subcommand(tmp_path, capsys):
    config_path = tmp_path / "config.toml"
    sqlite_path = tmp_path / "signals.sqlite3"
    config_path.write_text(
        f"""
[storage]
sqlite_path = \"{sqlite_path.as_posix()}\"
""".strip(),
        encoding="utf-8",
    )

    main([
        "init-db",
        "--config",
        str(config_path),
    ])

    assert sqlite_path.exists()
    assert capsys.readouterr().out.strip() == f"Initialized SQLite schema at {sqlite_path.as_posix()}"

