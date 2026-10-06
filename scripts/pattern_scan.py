"""Pattern scan over simulator trades: slices the existing summaries don't cover.

Usage: python pattern_scan.py <sim_out_dir> [--label ATM] [--data data] [--min-n 5]
"""
from __future__ import annotations

import argparse
import glob
import math
from pathlib import Path

import numpy as np
import pandas as pd

pd.set_option("display.width", 220)
pd.set_option("display.max_columns", 40)
pd.set_option("display.float_format", lambda v: f"{v:,.2f}")


def wilson_lb(wins: int, n: int, z: float = 1.2816) -> float:  # 80% one-sided lower bound
    if n == 0:
        return float("nan")
    p = wins / n
    denom = 1 + z * z / n
    centre = p + z * z / (2 * n)
    spread = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (centre - spread) / denom


def stats(g: pd.DataFrame) -> pd.Series:
    n = len(g)
    wins = int((g.pnl_usd > 0).sum())
    gross_win = g.loc[g.pnl_usd > 0, "pnl_usd"].sum()
    gross_loss = -g.loc[g.pnl_usd < 0, "pnl_usd"].sum()
    return pd.Series({
        "n": n,
        "win%": 100.0 * wins / n if n else np.nan,
        "wlb80%": 100.0 * wilson_lb(wins, n),
        "avg$": g.pnl_usd.mean() if n else np.nan,
        "total$": g.pnl_usd.sum(),
        "PF": gross_win / gross_loss if gross_loss > 0 else np.inf,
        "avg_hold": g.hold_minutes.mean() if n else np.nan,
    })


def by(df: pd.DataFrame, key, title: str, min_n: int = 1) -> None:
    print(f"\n== {title} ==")
    table = df.groupby(key, dropna=False, observed=True).apply(stats, include_groups=False)
    table = table[table.n >= min_n]
    print(table.to_string())


def load_trades(out_dir: Path, label: str) -> pd.DataFrame:
    t = pd.read_csv(out_dir / "trades.csv")
    t = t[(t.exit_reason != "no_entry") & t.entry_price.notna()]
    if label:
        t = t[t.label == label]
    t = t.copy()
    t["entry_dt"] = pd.to_datetime(t.entry_time, utc=True).dt.tz_convert("America/New_York")
    t["exit_dt"] = pd.to_datetime(t.exit_time, utc=True).dt.tz_convert("America/New_York")
    t["cross_dt"] = pd.to_datetime(t.cross_time, utc=True).dt.tz_convert("America/New_York")
    t["win"] = t.pnl_usd > 0
    t["month"] = t.entry_dt.dt.strftime("%Y-%m")
    t["quarter"] = t.entry_dt.dt.to_period("Q").astype(str)
    t["minute_of_day"] = t.entry_dt.dt.hour * 60 + t.entry_dt.dt.minute
    t = t.sort_values("entry_dt").reset_index(drop=True)
    return t


def add_day_context(t: pd.DataFrame, data: Path) -> pd.DataFrame:
    frames = []
    for sym in t.symbol.unique():
        d = pd.read_csv(data / "context" / f"{sym}_daily.csv", parse_dates=["date"])
        d = d.sort_values("date")
        d["prev_close"] = d.close.shift(1)
        d["gap_pct"] = 100.0 * (d.open / d.prev_close - 1)
        d["prev_day_ret_pct"] = 100.0 * (d.prev_close / d.close.shift(2) - 1)
        d["day_range_pct"] = 100.0 * (d.high - d.low) / d.open
        d["sma20"] = d.close.rolling(20).mean().shift(1)
        d["dist_sma20_pct"] = 100.0 * (d.prev_close / d.sma20 - 1)
        d["ret5_pct"] = 100.0 * (d.prev_close / d.close.shift(6) - 1)
        d["symbol"] = sym
        d["date_str"] = d.date.dt.strftime("%Y-%m-%d")
        frames.append(d[["symbol", "date_str", "gap_pct", "prev_day_ret_pct", "day_range_pct", "dist_sma20_pct", "ret5_pct", "opening_range_pct"]])
    ctx = pd.concat(frames)
    t = t.merge(ctx, left_on=["symbol", "date"], right_on=["symbol", "date_str"], how="left", suffixes=("", "_ctx"))
    vix = pd.read_csv(data / "context" / "VIX_History.csv")
    vix["date_str"] = pd.to_datetime(vix.DATE).dt.strftime("%Y-%m-%d")
    vix = vix.sort_values("date_str")
    vix["vix_open_vs_prev_pct"] = 100.0 * (vix.OPEN / vix.CLOSE.shift(1) - 1)
    vix["vix_chg_5d"] = vix.CLOSE.shift(1) - vix.CLOSE.shift(6)
    t = t.merge(vix[["date_str", "vix_open_vs_prev_pct", "vix_chg_5d"]], on="date_str", how="left")
    return t


def add_episode_context(t: pd.DataFrame, data: Path) -> pd.DataFrame:
    eps = []
    for sym in t.symbol.unique():
        for path in glob.glob(str(data / "episodes" / sym / f"{sym}_episodes_*.csv")):
            e = pd.read_csv(path)
            if not e.empty:
                eps.append(e)
    e = pd.concat(eps)
    e["cross_dt"] = pd.to_datetime(e.cross_time, utc=True).dt.tz_convert("America/New_York")
    e = e.sort_values(["symbol", "cross_dt"])
    e = e[(e.cross_dt.dt.hour * 60 + e.cross_dt.dt.minute) >= 570]
    e["prior_crosses_today"] = e.groupby(["symbol", "date"]).cumcount()
    e["prev_cross_dt"] = e.groupby(["symbol", "date"]).cross_dt.shift(1)
    e["min_since_prev_cross"] = (e.cross_dt - e.prev_cross_dt).dt.total_seconds() / 60.0
    e["next_cross_dt"] = e.groupby(["symbol", "date"]).cross_dt.shift(-1)
    e["min_to_next_cross"] = (e.next_cross_dt - e.cross_dt).dt.total_seconds() / 60.0
    e["crosses_today"] = e.groupby(["symbol", "date"]).cross_dt.transform("count")
    keep = e[["symbol", "cross_time", "prior_crosses_today", "min_since_prev_cross", "min_to_next_cross", "crosses_today", "spot"]]
    return t.merge(keep, on=["symbol", "cross_time"], how="left")


def add_entry_features(t: pd.DataFrame, data: Path) -> pd.DataFrame:
    rows = []
    for (sym, day), g in t.groupby(["symbol", "date"]):
        path = data / "evaluations" / sym / f"{sym}_evaluations_{day}.csv"
        if not path.exists():
            continue
        ev = pd.read_csv(path)
        if "timeframe" in ev.columns:
            ev = ev[ev.timeframe == "5m"]
        for _, tr in g.iterrows():
            m = ev[(ev.datetime == tr.detection_time) & (ev.direction == tr.direction)]  # detection row only: no post-entry leakage
            if m.empty:
                continue
            r = m.iloc[0]
            spot = float(r.last_price)
            sma30 = float(r.sma30_value) if pd.notna(r.sma30_value) else np.nan
            sma15 = float(r.sma15_value) if pd.notna(r.sma15_value) else np.nan
            rva = float(r.recent_volume_avg) if pd.notna(r.recent_volume_avg) else np.nan
            rows.append({
                "symbol": sym, "cross_time": tr.cross_time, "label": tr.label,
                "rvgi": r.rvgi, "rvgi_sma": r.rvgi_sma, "rvgi_vs_sma": r.rvgi_vs_sma, "rvgi_sign": r.rvgi_sign,
                "vwap_relation": r.vwap_relation, "ema9_relation": r.ema9_relation,
                "sma15_slope": r.sma15_slope, "sma30_slope": r.sma30_slope,
                "ext_sma30_bps": 1e4 * (spot / sma30 - 1) if sma30 == sma30 else np.nan,
                "sma_gap_bps": 1e4 * (sma15 / sma30 - 1) if sma30 == sma30 else np.nan,
                "vol_ratio": float(r.volume) / rva if rva == rva and rva > 0 else np.nan,
                "entry_grade_ev": r.grade,
            })
    if not rows:
        return t
    f = pd.DataFrame(rows)
    return t.merge(f, on=["symbol", "cross_time", "label"], how="left")


def bucket(series: pd.Series, edges: list[float], labels: list[str]) -> pd.Series:
    return pd.cut(series, bins=edges, labels=labels, include_lowest=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("out_dir")
    ap.add_argument("--label", default="ATM")
    ap.add_argument("--data", default="data")
    ap.add_argument("--min-n", type=int, default=5)
    args = ap.parse_args()
    out, data = Path(args.out_dir), Path(args.data)

    t = load_trades(out, args.label)
    t = add_day_context(t, data)
    t = add_episode_context(t, data)
    t = add_entry_features(t, data)
    mn = args.min_n

    print(f"=== {out} label={args.label or 'all'} ===")
    print("baseline:", stats(t).round(2).to_dict())
    eq = t.pnl_usd.cumsum()
    dd = (eq - eq.cummax()).min()
    print(f"max drawdown ${dd:,.0f}; trades {len(t)}; days with trades {t.date.nunique()}")
    gp = t.loc[t.pnl_usd > 0, "pnl_usd"].sort_values(ascending=False)
    if len(gp):
        print(f"top 5 winners = {gp.head(5).sum():,.0f} of gross profit {gp.sum():,.0f} ({100 * gp.head(5).sum() / gp.sum():.0f}%); net without top 5 = {t.pnl_usd.sum() - gp.head(5).sum():,.0f}")

    by(t, "quarter", "by quarter")
    by(t, "month", "by month")
    by(t, "exit_reason", "by exit reason")
    by(t, ["direction", "exit_reason"], "direction x exit reason")

    print("\n== hold minutes by exit reason (quantiles) ==")
    print(t.groupby("exit_reason").hold_minutes.quantile([.25, .5, .75]).unstack().to_string())
    print("\n== MAE of winners / MFE of losers (percent of entry premium) ==")
    w, l = t[t.win], t[~t.win]
    print("winners mae_pct quantiles:", w.mae_pct.quantile([.1, .25, .5, .75, .9]).round(1).to_dict())
    print("losers  mfe_pct quantiles:", l.mfe_pct.quantile([.1, .25, .5, .75, .9]).round(1).to_dict())
    for thr in (10, 15, 20, 25):
        print(f"  winners that first dipped below -{thr}%: {100 * (w.mae_pct <= -thr).mean():.0f}%   losers that first reached +{thr}%: {100 * (l.mfe_pct >= thr).mean():.0f}%")

    t["trade_num_today"] = t.groupby("date").cumcount() + 1
    t["prev_result_today"] = t.groupby("date").win.shift(1).map({True: "after win", False: "after loss"}).fillna("first")
    by(t, "trade_num_today", "nth trade of the day (all symbols)", mn)
    by(t, "prev_result_today", "after previous trade's result on the same day", mn)
    t["sym_trade_num_today"] = t.groupby(["symbol", "date"]).cumcount() + 1
    by(t, "sym_trade_num_today", "nth trade of the day per symbol", mn)

    paired = []
    for i, a in t.iterrows():
        twin = t[(t.index != i) & (t.date == a.date) & (t.direction == a.direction) & (t.symbol != a.symbol)
                 & ((t.entry_dt - a.entry_dt).abs() <= pd.Timedelta(minutes=10))]
        paired.append(len(twin) > 0)
    t["paired_cross"] = paired
    by(t, "paired_cross", "QQQ and SPY crossed same direction within 10 min (both taken)", mn)
    p = t[t.paired_cross]
    if len(p) >= 4:
        pp = p.groupby(["date", "direction"]).agg(n=("pnl_usd", "size"), both_win=("win", "all"), both_loss=("win", lambda s: (~s).all()), total=("pnl_usd", "sum"))
        pp = pp[pp.n >= 2]
        first_only = p.sort_values("entry_dt").groupby(["date", "direction"]).pnl_usd.first().sum()
        print(f"   paired events: {len(pp)}; both win {int(pp.both_win.sum())}, both lose {int(pp.both_loss.sum())}, split {int(len(pp) - pp.both_win.sum() - pp.both_loss.sum())}; "
              f"avg per event ${pp.total.mean():,.0f}; first-of-pair only ${first_only:,.0f} vs both ${p.pnl_usd.sum():,.0f}")

    by(t, "prior_crosses_today", "prior RTH crosses that day before this one (per symbol)", mn)
    t["since_prev_bucket"] = bucket(t.min_since_prev_cross.fillna(999), [0, 20, 40, 80, 1000], ["<20", "20-40", "40-80", "80+/first"])
    by(t, "since_prev_bucket", "minutes since previous cross (same symbol)", mn)
    t["next_cross_bucket"] = bucket(t.min_to_next_cross.fillna(999), [0, 15, 30, 60, 1000], ["<15", "15-30", "30-60", "60+/none"])
    by(t, "next_cross_bucket", "minutes until the NEXT opposite cross (hindsight: whipsaw check)", mn)
    t["crosses_bucket"] = bucket(t.crosses_today, [0, 2, 4, 6, 50], ["1-2", "3-4", "5-6", "7+"])
    by(t, "crosses_bucket", "total RTH crosses that day (hindsight: chop days)", mn)

    t["gap_bucket"] = bucket(t.gap_pct, [-99, -0.5, -0.15, 0.15, 0.5, 99], ["gap<-0.5%", "-0.5..-0.15", "flat", "0.15..0.5", "gap>0.5%"])
    by(t, "gap_bucket", "opening gap vs prior close", mn)
    t["gap_dir"] = np.where(t.gap_pct.abs() < 0.15, "flat", np.where((t.gap_pct > 0) == (t.direction == "bull"), "gap with trade", "gap against trade"))
    by(t, ["direction", "gap_dir"], "gap direction relative to the trade", mn)
    t["prev_ret_bucket"] = bucket(t.prev_day_ret_pct, [-99, -1, -0.3, 0.3, 1, 99], ["<-1%", "-1..-0.3", "flat", "0.3..1", ">1%"])
    by(t, "prev_ret_bucket", "prior day's return", mn)
    t["dist_sma20_bucket"] = bucket(t.dist_sma20_pct, [-99, -2, 0, 1, 2, 99], ["<-2%", "-2..0", "0..1", "1..2", ">2%"])
    by(t, ["direction", "dist_sma20_bucket"], "prior close distance from SMA20 (trend strength) x direction", mn)
    t["ret5_bucket"] = bucket(t.ret5_pct, [-99, -2, -0.5, 0.5, 2, 99], ["<-2%", "-2..-0.5", "flat", "0.5..2", ">2%"])
    by(t, ["direction", "ret5_bucket"], "5-day return x direction", mn)
    t["vix_open_bucket"] = bucket(t.vix_open_vs_prev_pct, [-99, -5, -1, 1, 5, 99], ["vix<-5%", "-5..-1", "flat", "1..5", "vix>5%"])
    by(t, "vix_open_bucket", "VIX open vs its prior close", mn)
    t["vix5_bucket"] = bucket(t.vix_chg_5d, [-99, -2, 0, 2, 99], ["falling>2", "falling", "rising", "rising>2"])
    by(t, "vix5_bucket", "VIX 5-day change (points)", mn)
    t["or_bucket"] = bucket(t.opening_range_pct, [0, 0.3, 0.45, 0.6, 9], ["<0.3%", "0.3-0.45", "0.45-0.6", ">0.6%"])
    by(t, "or_bucket", "opening range % (first 15 min)", mn)
    by(t, "turbulence", "turbulence label", mn)
    by(t, ["vix_regime", "direction"], "VIX regime x direction", mn)

    t["entry_bucket"] = bucket(t.minute_of_day, [569, 585, 600, 630, 660, 720, 780, 960], ["9:30-9:45", "9:45-10:00", "10:00-10:30", "10:30-11:00", "11:00-12:00", "12:00-13:00", "13:00+"])
    by(t, "entry_bucket", "entry time bucket", mn)
    by(t, ["direction", "entry_bucket"], "direction x entry time", mn)
    by(t, ["day_of_week", "direction"], "weekday x direction", mn)
    t["delay_bucket"] = bucket(t.entry_delay_min, [-1, 0, 2, 5, 15], ["0", "1-2", "3-5", "6-15"])
    by(t, "delay_bucket", "minutes from detection to entry", mn)
    t["lag_bucket2"] = bucket(t.lag_min, [-999, 0, 2, 5, 10, 999], ["<=0", "0-2", "2-5", "5-10", ">10"])
    by(t, ["direction", "lag_bucket2"], "1m->5m cross lag x direction", mn)
    by(t, ["entry_grade", "direction"], "grade x direction", mn)
    by(t, "bar_minutes_elapsed", "minute of the 5m bar at detection", mn)

    t["prem_bucket"] = bucket(t.entry_price, [0, 1.0, 1.5, 2.0, 2.5, 99], ["<1.00", "1.00-1.50", "1.50-2.00", "2.00-2.50", ">2.50"])
    by(t, "prem_bucket", "entry premium", mn)
    by(t, ["direction", "prem_bucket"], "direction x entry premium", mn)

    if "rvgi" in t.columns:
        by(t, ["direction", "rvgi_vs_sma"], "RVGI vs its SMA x direction", mn)
        by(t, ["direction", "rvgi_sign"], "RVGI sign x direction", mn)
        by(t, "volume_grade", "volume grade at entry", mn)
        by(t, "one_min_agreement", "1m agreement at entry", mn)
        t["ext_bucket"] = bucket(t.ext_sma30_bps.abs(), [0, 5, 10, 20, 1000], ["<5bp", "5-10", "10-20", ">20bp"])
        by(t, ["direction", "ext_bucket"], "price extension from SMA30 (abs bps) x direction", mn)
        t["gap_sma_bucket"] = bucket(t.sma_gap_bps.abs(), [0, 1, 2, 4, 1000], ["<1bp", "1-2", "2-4", ">4bp"])
        by(t, "gap_sma_bucket", "SMA15-SMA30 separation at entry (abs bps)", mn)
        t["volr_bucket"] = bucket(t.vol_ratio, [0, 0.9, 1.2, 1.6, 99], ["<0.9", "0.9-1.2", "1.2-1.6", ">1.6"])
        by(t, "volr_bucket", "trigger volume / prior-5 average", mn)
        t["slope_agree"] = np.where(((t.sma30_slope > 0) == (t.direction == "bull")), "sma30 slope with trade", "sma30 slope against")
        by(t, ["direction", "slope_agree"], "SMA30 slope direction x trade direction", mn)

    s = t.win.astype(int).values
    if len(s) > 10:
        ac = np.corrcoef(s[:-1], s[1:])[0, 1]
        cur, worst = 0, 0
        for v in s:
            cur = cur + 1 if v == 0 else 0
            worst = max(worst, cur)
        print(f"\n== sequence == lag-1 win autocorrelation {ac:+.2f}; longest losing streak {worst}")
        roll = pd.Series(s).rolling(20).mean()
        print("rolling 20-trade win rate, last 6 points:", [f"{v:.0%}" for v in roll.dropna().iloc[-6:]])

    t.to_csv(out / "pattern_scan_trades.csv", index=False)
    print(f"\nenriched trades written to {out / 'pattern_scan_trades.csv'}")


if __name__ == "__main__":
    main()
