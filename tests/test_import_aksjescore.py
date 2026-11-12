import sqlite3
from datetime import date, timedelta

import pandas as pd

from app.import_aksjescore import export_scores
from app.strategy import ScoreBook


def test_export_maps_tickers_and_rescales(tmp_path):
    today, old = date.today().isoformat(), (date.today() - timedelta(days=900)).isoformat()
    db = tmp_path / "a.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE daily_scores (ticker TEXT, score_date TEXT, total REAL)")
    con.executemany("INSERT INTO daily_scores VALUES (?,?,?)", [
        ("EQNR.OL", today, 80.0), ("DNB.OL", today, 25.0), ("VOLV-B.ST", today, 90.0),
        ("UNKNOWN.OL", today, 70.0), ("EQNR.OL", old, 10.0), ("TEL.OL", today, 150.0)])
    con.commit(); con.close()
    uni = tmp_path / "u.csv"
    uni.write_text("symbol,currency,primary_exchange,market,enabled,yahoo_ticker\n"
                   "EQNR,NOK,X,Norway,true,EQNR.OL\nDNB,NOK,X,Norway,true,DNB.OL\n"
                   "TEL,NOK,X,Norway,true,TEL.OL\nVOLV B,SEK,X,Sweden,false,VOLV-B.ST\n")
    out = tmp_path / "s" / "scores.csv"
    assert export_scores(db, uni, out) == 2          # disabled, unknown, too old, out of range dropped
    df = pd.read_csv(out).set_index("symbol")
    assert df.loc["EQNR", "score"] == 0.6 and df.loc["DNB", "score"] == -0.5
    assert ScoreBook.from_csv(out).get("EQNR", date.today()) == 0.6


def test_compare_closes_flags_disagreements(tmp_path):
    from datetime import date
    from app.import_aksjescore import compare_closes
    today, yday = date.today(), date.today() - timedelta(days=1)
    db = tmp_path / "a.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE daily_scores (ticker TEXT, score_date TEXT, close_price REAL)")
    rows = [(f"S{i}.OL", yday.isoformat(), 100.0) for i in range(25)]
    rows += [("S0.OL", today.isoformat(), 101.0)]                    # too few rows today: skip that date
    con.executemany("INSERT INTO daily_scores VALUES (?,?,?)", rows); con.commit(); con.close()
    bars = {f"S{i}": pd.DataFrame({"close": [100.0, 100.4]}, index=[yday, today]) for i in range(25)}
    bars["S3"] = pd.DataFrame({"close": [90.0, 90.0]}, index=[yday, today])   # 10 % off
    out = compare_closes(db, bars, {f"S{i}": f"S{i}.OL" for i in range(25)})
    assert out["date"] == yday.isoformat() and out["compared"] == 25
    assert out["mismatched"] == [("S3", 90.0, 100.0)]
    assert compare_closes(tmp_path / "missing.db", bars, {}) is None
    # a close that aksjescore merely carried over from its previous day is not compared
    con = sqlite3.connect(db)
    two_days = (today - timedelta(days=2)).isoformat()
    con.executemany("INSERT INTO daily_scores VALUES (?,?,?)",
                    [("S3.OL", two_days, 100.0)] + [(f"S{i}.OL", two_days, 99.0) for i in range(25) if i != 3])
    con.commit(); con.close()
    out = compare_closes(db, bars, {f"S{i}": f"S{i}.OL" for i in range(25)})
    assert out["compared"] == 24 and out["mismatched"] == []
