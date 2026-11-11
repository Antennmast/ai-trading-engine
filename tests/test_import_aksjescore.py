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
