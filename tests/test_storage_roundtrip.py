import sqlite3
from datetime import UTC, datetime

from src.main import _read_evaluations_from_db
from src.models import Direction, Grade, OutcomeGrade, OutcomeResult, SetupEvaluation, StrikeBias, Timeframe
from src.storage import SQLiteLogger


def _evaluation() -> SetupEvaluation:
    return SetupEvaluation(
        symbol="QQQ",
        timestamp=datetime(2026, 3, 24, 15, 35, tzinfo=UTC),
        timeframe=Timeframe.FIVE_MINUTE,
        direction=Direction.BULL,
        last_price=586.24,
        vwap_relation="above",
        ema9_relation="above",
        sma15_value=585.0,
        sma30_value=583.0,
        sma_trend_relation="bullish",
        sma_cross_signal="bull",
        sma_cross_status="active",
        sma_cross_time=datetime(2026, 3, 24, 15, 20, tzinfo=UTC),
        sma15_slope=0.35,
        sma30_slope=0.18,
        rvgi=0.22,
        rvgi_sma=0.18,
        rvgi_vs_sma="above",
        rvgi_sign="positive",
        volume=2200,
        recent_volume_avg=1800,
        rolling_volume_avg=1750,
        volume_grade="acceptable",
        one_min_agreement="yes",
        grade=Grade.B,
        strike_bias=StrikeBias.ATM,
        strike_bias_reason="default",
        passed_conditions=["price aligned with VWAP"],
        weak_conditions=["RVGI crossover is incomplete"],
        failed_conditions=[],
        rationale="Bullish structure is intact, but momentum confirmation is incomplete.",
        forward_return_3m=0.0019,
        forward_return_5m=0.0036,
        forward_return_10m=0.0028,
        forward_return_15m=0.0032,
        forward_return_30m=0.0034,
        pop_outcome=OutcomeResult.WIN,
        pop_outcome_horizon="3m",
        pop_grade=OutcomeGrade.B,
    )


def test_sqlite_logger_migrates_legacy_schema_and_roundtrips_outcome_fields(base_config, tmp_path):
    db_path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(db_path) as connection:
        connection.executescript(
            """
            CREATE TABLE runs (
                run_id TEXT PRIMARY KEY,
                mode TEXT NOT NULL,
                started_at TEXT NOT NULL,
                config_hash TEXT NOT NULL,
                source TEXT NOT NULL
            );

            CREATE TABLE evaluated_setups (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                symbol TEXT NOT NULL,
                datetime TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                direction TEXT NOT NULL,
                last_price REAL NOT NULL,
                vwap_relation TEXT,
                ema9_relation TEXT,
                sma15_value REAL,
                sma30_value REAL,
                sma_trend_relation TEXT,
                sma_cross_signal TEXT,
                sma_cross_status TEXT,
                sma_cross_time TEXT,
                sma15_slope REAL,
                sma30_slope REAL,
                rvgi REAL,
                rvgi_sma REAL,
                rvgi_vs_sma TEXT,
                rvgi_sign TEXT,
                volume REAL,
                recent_volume_avg REAL,
                rolling_volume_avg REAL,
                volume_grade TEXT,
                one_min_agreement TEXT,
                grade TEXT,
                strike_bias TEXT,
                strike_bias_reason TEXT,
                passed_conditions TEXT,
                weak_conditions TEXT,
                failed_conditions TEXT,
                rationale TEXT,
                alert_sent INTEGER,
                forward_return_3m REAL,
                forward_return_5m REAL,
                forward_return_10m REAL,
                forward_return_15m REAL
            );

            CREATE TABLE alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                symbol TEXT NOT NULL,
                datetime TEXT NOT NULL,
                direction TEXT NOT NULL,
                grade TEXT NOT NULL,
                strike_bias TEXT NOT NULL,
                delivered INTEGER NOT NULL,
                transport_message TEXT NOT NULL,
                title TEXT NOT NULL,
                message TEXT NOT NULL
            );
            """
        )

    logger = SQLiteLogger(str(db_path))
    logger.initialize()

    with sqlite3.connect(db_path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(evaluated_setups)")}

    assert {"forward_return_30m", "pop_outcome", "pop_outcome_horizon", "pop_grade"}.issubset(columns)

    run_id = logger.create_run(mode="replay", config=base_config, source="tests/fixtures/sample_intraday.csv")
    logger.log_evaluations(run_id, [_evaluation()])

    rows = _read_evaluations_from_db(str(db_path))

    assert len(rows) == 1
    assert rows[0].forward_return_30m == 0.0034
    assert rows[0].pop_outcome == OutcomeResult.WIN
    assert rows[0].pop_outcome_horizon == "3m"
    assert rows[0].pop_grade == OutcomeGrade.B
