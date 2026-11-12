"""Copy scores from an aksjescore SQLite database into signals/scores.csv.

    python -m app.import_aksjescore path/to/aksjescore.db

aksjescore's total (0..100) becomes a score in -1..1: (total - 50) / 50. With the
default thresholds the Sentiment model then buys only at total >= 60 and exits at
total <= 25. Only instruments enabled in the universe are exported.
"""
from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

import pandas as pd


def export_scores(db_path, universe_csv, out_csv, days: int = 30) -> int:
    uni = pd.read_csv(universe_csv, dtype={"symbol": str})
    uni = uni[uni["enabled"].astype(str).str.lower() == "true"]
    by_ticker = dict(zip(uni["yahoo_ticker"], uni["symbol"]))
    con = sqlite3.connect(f"file:{Path(db_path).resolve()}?mode=ro", uri=True)
    try:
        rows = pd.read_sql_query(
            "SELECT ticker, score_date AS date, total FROM daily_scores "
            "WHERE score_date >= date('now', ?) ORDER BY score_date, ticker",
            con, params=(f"-{int(days)} days",))
    finally:
        con.close()
    rows = rows[rows["ticker"].isin(by_ticker) & rows["total"].between(0, 100)]
    out = pd.DataFrame({
        "symbol": rows["ticker"].map(by_ticker),
        "date": rows["date"],
        "score": ((rows["total"] - 50) / 50).round(4),
    })
    Path(out_csv).parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(out_csv, index=False)
    return len(out)


def compare_closes(db_path, bars: dict, tickers: dict[str, str], tolerance: float = 0.01) -> dict | None:
    """Cross-check our closes against aksjescore's for the newest date both have.

    The two projects fetch prices independently, so a disagreement points at bad data.
    Returns {"date", "compared", "mismatched": [(symbol, ours, theirs), ...]} or None.
    """
    if not Path(db_path).exists():
        return None
    con = sqlite3.connect(f"file:{Path(db_path).resolve()}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "SELECT ticker, score_date, close_price FROM daily_scores WHERE close_price > 0 "
            "AND score_date >= date('now', '-10 days')").fetchall()
    except sqlite3.Error:
        return None
    finally:
        con.close()
    by_ticker = {t: s for s, t in tickers.items()}
    theirs: dict[str, dict[str, float]] = {}
    for ticker, day, close in rows:
        if ticker in by_ticker:
            theirs.setdefault(day, {})[by_ticker[ticker]] = float(close)
    for day in sorted(theirs, reverse=True):               # newest date that we also have
        pairs = []
        for sym, close in theirs[day].items():
            df = bars.get(sym)
            if df is None:
                continue
            ours = [float(c) for d, c in zip(df.index, df["close"]) if d.isoformat() == day]
            if ours:
                pairs.append((sym, ours[0], close))
        if len(pairs) >= 20:
            bad = [(s, round(o, 4), round(c, 4)) for s, o, c in pairs if abs(o / c - 1) > tolerance]
            return {"date": day, "compared": len(pairs), "mismatched": sorted(bad)}
    return None


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("db")
    p.add_argument("--universe", default="config/universe.csv")
    p.add_argument("--out", default="signals/scores.csv")
    a = p.parse_args()
    n = export_scores(a.db, a.universe, a.out)
    print(f"[aksjescore] wrote {n} score rows to {a.out}")


if __name__ == "__main__":
    main()
