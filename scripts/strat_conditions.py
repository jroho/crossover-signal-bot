"""How each strategy did in conditions like today's: recent windows, VIX regime (prior close), data trend bias, rolling Sharpe."""
import numpy as np, pandas as pd
vix = pd.read_csv("data/context/VIX_History.csv"); vix["day"] = pd.to_datetime(vix.DATE).dt.normalize()
vix = vix.set_index("day").CLOSE.shift(1)  # prior close, as the bot uses
regime = pd.cut(vix, [0, 15, 20, 30, 999], labels=["low<15", "mid15-20", "high20-30", "extreme"])
def bias(sym):
    d = pd.read_csv(f"data/context/{sym}_daily.csv"); d["day"] = pd.to_datetime(d.date); d = d.set_index("day").close
    prev, sma, ret = d.shift(1), d.shift(1).rolling(20).mean(), d.shift(1) / d.shift(6) - 1
    return pd.Series(np.select([(prev > sma) & (ret > 0), (prev < sma) & (ret < 0)], ["bull", "bear"], "neutral"), index=d.index)
def table(g):
    daily = g.groupby("day").bps.sum()
    return pd.Series({"trades": len(g), "win%": round((g.bps > 0).mean() * 100, 1), "bps/trade": round(g.bps.mean(), 1),
                      "pct/yr": round(daily.sum() / 100 / max(g.day.nunique(), 1) * 252, 1)})
out = {}
for f, label in [("S1", "S1 noise"), ("S2", "S2 ORB")]:
    t = pd.read_csv(f"logs/sim/strat_{f}.csv"); t["day"] = pd.to_datetime(t.day)
    for sym, g in t.groupby("sym"):
        g = g.copy(); g["vix"] = g.day.map(regime); g["trend"] = g.day.map(bias(sym))
        g["window"] = np.select([g.day >= "2026-04-09", g.day >= "2025-10-09"], ["last 6m", "6-12m ago"], "older")
        print(f"\n=== {label} {sym}")
        for k in ("window", "vix", "trend"):
            print(g.groupby(k, observed=True).apply(table, include_groups=False).T.to_string())
        if label == "S1 noise":
            all_days = pd.Series(0.0, index=pd.bdate_range("2019-01-02", "2026-10-08"))
            d = g.groupby("day").bps.sum(); all_days.loc[d.index.intersection(all_days.index)] = d
            roll = all_days.rolling(252).mean() / all_days.rolling(252).std() * np.sqrt(252)
            print("rolling 12m Sharpe at year-ends:", {str(k.date()): round(v, 2) for k, v in roll.resample("YE").last().dropna().items()}, "latest", round(roll.iloc[-1], 2))
            out[sym] = all_days
p = pd.read_csv("logs/sim/strat_S4.csv"); p["day"] = pd.to_datetime(p.day); p["vix"] = p.day.map(regime)
print("\n=== S4 premium proxy by VIX regime (edge = implied - realized, bps)")
print(p.groupby("vix", observed=True).agg(days=("edge_bps", "size"), edge=("edge_bps", lambda x: round(x.mean(), 1)),
      realized_gt=("edge_bps", lambda x: round((x < 0).mean() * 100, 1)), worst=("edge_bps", lambda x: round(x.min(), 0))).T.to_string())
print("last 6m edge", round(p[p.day >= "2026-04-09"].edge_bps.mean(), 1))
x = pd.read_csv("logs/sim/ext_policy/trades.csv"); x = x[x.entry_time.notna()]; x["day"] = pd.to_datetime(x.date)
cx = x.groupby("day").pnl_usd.sum().reindex(out["QQQ"].index).fillna(0)
m = (out["QQQ"].index >= "2024-02-01")
print(f"\ncorrelation of daily P&L, S1-QQQ vs crossover policy (2024-02..2026-10): {np.corrcoef(out['QQQ'][m], cx[m])[0,1]:.2f}; S1-QQQ vs S1-SPY: {np.corrcoef(out['QQQ'], out['SPY'])[0,1]:.2f}")
