from src.data import CsvReplayAdapter
from src.indicators import build_indicator_bundle


def test_vwap_resets_at_new_session(base_config, sample_csv_path):
    candles = CsvReplayAdapter().load_candles(sample_csv_path, ["QQQ"])
    one_min_bundle, _ = build_indicator_bundle(candles, base_config)
    frame = one_min_bundle.dataframe[one_min_bundle.dataframe["symbol"] == "QQQ"].copy()

    session_starts = frame.groupby(frame["timestamp"].dt.date).head(1).reset_index(drop=True)
    assert len(session_starts) == 2
    first_row = session_starts.iloc[0]
    second_row = session_starts.iloc[1]

    first_typical = (first_row["high"] + first_row["low"] + first_row["close"]) / 3.0
    second_typical = (second_row["high"] + second_row["low"] + second_row["close"]) / 3.0

    assert abs(first_row["vwap"] - first_typical) < 1e-9
    assert abs(second_row["vwap"] - second_typical) < 1e-9
    assert abs(second_row["vwap"] - frame.iloc[0]["vwap"]) > 1.0
