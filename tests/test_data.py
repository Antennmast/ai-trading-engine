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


def _chart(closes, opens=None, gmtoffset=3600, events=None):
    base = 1794384000                       # 2026-11-11 08:00 UTC = 09:00 in Oslo
    n = len(closes)
    stamps = [base + 86400 * (i - (n - 1)) for i in range(n)]
    opens = opens or closes
    return {"chart": {"result": [{
        "meta": {"gmtoffset": gmtoffset}, "timestamp": stamps, "events": events or {},
        "indicators": {"quote": [{"open": opens, "high": [c and c + 1 for c in closes],
                                  "low": [c and c - 1 for c in closes], "close": closes,
                                  "volume": [1000] * n}]}}]}}, stamps


def test_parse_chart_keeps_the_newest_bar_and_drops_bars_without_close():
    from app.data.providers import parse_chart
    payload, stamps = _chart([10.0, None, 12.0])
    df = parse_chart(payload)
    assert list(df.index) == [date(2026, 11, 9), date(2026, 11, 11)]      # a null close mid-series is dropped
    assert df.loc[date(2026, 11, 11), "close"] == 12.0 and df.loc[date(2026, 11, 11), "open"] == 12.0
    # a late-evening UTC timestamp still lands on the exchange's own date
    payload, _ = _chart([10.0, 11.0], gmtoffset=3600)
    payload["chart"]["result"][0]["timestamp"][-1] = 1794438000           # 2026-11-11 23:00 UTC
    assert parse_chart(payload).index[-1] == date(2026, 11, 12)


def test_parse_chart_reads_dividends_and_splits_and_index_mode():
    from app.data.providers import parse_chart
    payload, stamps = _chart([10.0, 11.0, 12.0])
    payload["chart"]["result"][0]["events"] = {
        "dividends": {str(stamps[1]): {"amount": 0.5, "date": stamps[1]}},
        "splits": {str(stamps[2]): {"date": stamps[2], "numerator": 5, "denominator": 1}}}
    df = parse_chart(payload)
    assert df["dividends"].tolist() == [0.0, 0.5, 0.0] and df["splits"].tolist() == [0.0, 0.0, 5.0]
    # indices often come with zero open/high/low: usable when only the close is needed
    payload, _ = _chart([2000.0, 2010.0], opens=[0.0, 0.0])
    assert len(parse_chart(payload)) == 0
    idx = parse_chart(payload, closes_only=True)
    assert idx["close"].tolist() == [2000.0, 2010.0] and (idx["open"] > 0).all()


def test_newest_bar_gets_its_close_from_the_market_price_once_the_session_is_over():
    from app.data.providers import parse_chart
    payload, stamps = _chart([382.2, None], opens=[381.0, 385.4])
    quote = payload["chart"]["result"][0]["indicators"]["quote"][0]
    quote["high"], quote["low"] = [383.8, 395.0], [373.2, 385.4]
    meta = payload["chart"]["result"][0]["meta"]
    close_time = stamps[-1] + 7 * 3600 + 26 * 60                          # 16:26 Oslo time, same day
    meta.update(regularMarketPrice=395.2, regularMarketTime=close_time)
    df = parse_chart(payload, now_ts=close_time + 3600)
    bar = df.loc[date(2026, 11, 11)]
    assert (bar["open"], bar["close"], bar["high"], bar["low"]) == (385.4, 395.2, 395.2, 385.4)
    # still trading (price is only minutes old): the unfinished bar is left out
    assert list(parse_chart(payload, now_ts=close_time + 60).index) == [date(2026, 11, 10)]
    # a price from another day is never used for this bar
    meta["regularMarketTime"] = close_time - 86400
    assert list(parse_chart(payload, now_ts=close_time + 3600).index) == [date(2026, 11, 10)]
    # index mode: zero open/high/low on the newest bar is fine
    payload, stamps = _chart([2081.4, None], opens=[2079.9, 0.0])
    q2 = payload["chart"]["result"][0]["indicators"]["quote"][0]
    q2["high"], q2["low"] = [2084.9, 0.0], [2068.4, 0.0]
    payload["chart"]["result"][0]["meta"].update(regularMarketPrice=2106.49, regularMarketTime=stamps[-1] + 9 * 3600)
    idx = parse_chart(payload, closes_only=True, now_ts=stamps[-1] + 12 * 3600)
    assert idx["close"].tolist() == [2081.4, 2106.49]
