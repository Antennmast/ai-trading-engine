from datetime import date, datetime
from zoneinfo import ZoneInfo

import pandas as pd

from app.data.providers import clean, last_completed_bar_date

OSLO = ZoneInfo("Europe/Oslo")


def test_todays_bar_excluded_until_session_is_over():
    assert last_completed_bar_date(datetime(2026, 11, 11, 12, 0, tzinfo=OSLO)) == date(2026, 11, 10)
    assert last_completed_bar_date(datetime(2026, 11, 11, 18, 5, tzinfo=OSLO)) == date(2026, 11, 11)


def test_cron_time_is_after_close_in_both_dst_regimes():
    utc = ZoneInfo("UTC")
    assert last_completed_bar_date(datetime(2026, 1, 15, 17, 15, tzinfo=utc)) == date(2026, 1, 15)
    assert last_completed_bar_date(datetime(2026, 7, 15, 17, 15, tzinfo=utc)) == date(2026, 7, 15)


def test_clean_drops_bad_rows_duplicates_and_future_bars():
    idx = pd.to_datetime(["2025-01-02", "2025-01-03", "2025-01-03", "2025-01-06", "2025-01-07"]).tz_localize(OSLO)
    raw = pd.DataFrame({"Open": [1, 2, 2.5, 0, 4], "High": [1, 2, 2.5, 3, 4], "Low": [1, 2, 2.5, 3, 4],
                        "Close": [1, 2, 2.5, 3, 4], "Volume": [9] * 5, "Dividends": [0] * 5}, index=idx)
    df = clean(raw, cutoff=date(2025, 1, 6))
    assert list(df.index) == [date(2025, 1, 2), date(2025, 1, 3)]   # zero-price and post-cutoff bars gone
    assert df.loc[date(2025, 1, 3), "close"] == 2.5                 # last duplicate wins


def test_index_curve_starts_at_models_first_day():
    from app.report import index_curve
    close = pd.Series([100.0, 200.0, 210.0, 190.0],
                      index=[date(2026, 11, 9), date(2026, 11, 10), date(2026, 11, 11), date(2026, 11, 12)])
    models = [{"curve": [["2026-11-10", 100000], ["2026-11-11", 100500]]}, {"curve": []}]
    out = index_curve(("OSEBX", close), models)
    assert out["curve"] == [["2026-11-10", 0.0], ["2026-11-11", 5.0], ["2026-11-12", -5.0]]
    assert out["return_pct"] == -5.0 and out["name"] == "OSEBX"
    assert index_curve(("OSEBX", None), models) is None and index_curve(None, models) is None
    assert index_curve(("OSEBX", close), [{"curve": []}]) is None


def test_universe_file_is_consistent():
    u = pd.read_csv("config/universe.csv", dtype=str)
    assert u["symbol"].is_unique and u["yahoo_ticker"].is_unique
    assert set(u["currency"]) == {"NOK", "SEK", "DKK", "EUR"}
    suffix = {"NOK": ".OL", "SEK": ".ST", "DKK": ".CO", "EUR": ".HE"}
    assert all(t.endswith(suffix[c]) for t, c in zip(u["yahoo_ticker"], u["currency"]))
