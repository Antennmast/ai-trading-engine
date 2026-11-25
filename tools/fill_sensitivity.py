"""How much do results depend on getting the opening price?

For every fill so far, compare the open with other prices from the same day:
  worst case : buys at the day's high, sells at the day's low
  late       : the day's typical price (high + low + close) / 3
Prints the change in each model's result in NOK and percentage points.
"""
import csv
import json
from pathlib import Path

import pandas as pd

from app.data.providers import YahooProvider

uni = {r["symbol"]: r for r in csv.DictReader(open("config/universe.csv"))}
models = json.load(open("docs/data.json"))["models"]
trades = {}
for m in models:
    p = Path("state") / m["key"] / "trades.csv"
    if p.exists():
        t = pd.read_csv(p, dtype={"symbol": str})
        trades[m["key"]] = t[t["side"].isin(["BUY", "SELL"])]
symbols = sorted({s for t in trades.values() for s in t["symbol"]})
bars = YahooProvider(period="3mo").history({s: uni[s]["yahoo_ticker"] for s in symbols})
start = 100_000
print(f"{'model':16s}{'result':>9s}{'worst':>9s}{'late':>9s}   (percentage points; fills {'':>1s})")
for m in models:
    t = trades.get(m["key"])
    if t is None or t.empty:
        continue
    worst = late = 0.0
    missing = 0
    for r in t.itertuples():
        df = bars.get(r.symbol)
        if df is None or pd.Timestamp(r.date).date() not in df.index:
            missing += 1
            continue
        bar = df.loc[pd.Timestamp(r.date).date()]
        # the price the engine used before slippage (open, close or stop)
        base = r.price / (1 + 0.0005) if r.side == "BUY" else r.price / (1 - 0.0005)
        typical = (bar["high"] + bar["low"] + bar["close"]) / 3
        sign = 1 if r.side == "BUY" else -1                   # paying more on a buy hurts; less on a sell hurts
        alt_worst = bar["high"] if r.side == "BUY" else bar["low"]
        worst += sign * (alt_worst - base) * r.qty * r.fx
        late += sign * (typical - base) * r.qty * r.fx
    print(f"{m['key']:16s}{m['return_pct']:+8.2f}%{m['return_pct'] - worst / start * 100:+8.2f}%"
          f"{m['return_pct'] - late / start * 100:+8.2f}%   ({len(t)} fills" + (f", {missing} without bar" if missing else "") + ")")
mom = trades.get("momentum")
if mom is not None:
    print("\nMomentum, per purchase (open vs high vs typical):")
    for r in mom[mom["side"] == "BUY"].itertuples():
        bar = bars[r.symbol].loc[pd.Timestamp(r.date).date()]
        print(f"  {uni[r.symbol]['name'].title()[:22]:22s} open {bar['open']:9.2f}  high {bar['high']:9.2f} ({bar['high']/bar['open']-1:+5.1%})"
              f"  typical {(bar['high']+bar['low']+bar['close'])/3:9.2f} ({(bar['high']+bar['low']+bar['close'])/3/bar['open']-1:+5.1%})")
