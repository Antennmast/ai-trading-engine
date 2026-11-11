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


def export_scores(db_path, universe_csv, out_csv, days: int = 400) -> int:
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
