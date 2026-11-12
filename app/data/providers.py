"""Price data providers. Each returns daily OHLCV with a datetime.date index."""
from __future__ import annotations

from datetime import date as Date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

COLUMNS = ["open", "high", "low", "close", "volume"]
MARKET_TZ = ZoneInfo("Europe/Oslo")
BAR_FINAL_AFTER = time(18, 0)       # Oslo closes 16:25, Stockholm/Helsinki 17:30 CET; Yahoo is delayed


def last_completed_bar_date(now: datetime | None = None) -> Date:
    """Today's bar only counts once the session is over; otherwise use up to yesterday."""
    now = (now or datetime.now(MARKET_TZ)).astimezone(MARKET_TZ)
    if now.time() >= BAR_FINAL_AFTER:
        return now.date()
    return (now - pd.Timedelta(days=1)).date()


def clean(df: pd.DataFrame, cutoff: Date | None = None) -> pd.DataFrame:
    """OHLCV plus `dividends` (cash per share on the ex-date) and `splits` (ratio, 0 = none)."""
    df = df.rename(columns=lambda c: str(c).lower().replace("stock splits", "splits"))
    for extra in ("dividends", "splits"):
        if extra not in df.columns:
            df[extra] = 0.0
    df = df[COLUMNS + ["dividends", "splits"]].dropna(subset=["open", "high", "low", "close"])
    df[["dividends", "splits"]] = df[["dividends", "splits"]].fillna(0.0)
    df.index = pd.to_datetime(df.index).date
    df = df[~pd.Index(df.index).duplicated(keep="last")].sort_index()
    df = df[(df[["open", "high", "low", "close"]] > 0).all(axis=1)]
    if cutoff:
        df = df[[d <= cutoff for d in df.index]]
    return df


def total_return_view(bars: pd.DataFrame) -> pd.DataFrame:
    """Prices adjusted for dividends, anchored so the LAST bar equals the traded price.

    Signals are computed on this view so an ex-dividend drop is not mistaken for a loss
    or a broken trend. Fills always use the raw prices.
    """
    if "dividends" not in bars.columns or not (bars["dividends"] > 0).any():
        return bars
    step = 1.0 - (bars["dividends"] / bars["close"].shift(1)).fillna(0.0).clip(upper=0.9)
    factor = step[::-1].cumprod()[::-1].shift(-1).fillna(1.0)   # product of later ex-dates
    out = bars.copy()
    for col in ("open", "high", "low", "close"):
        out[col] = bars[col] * factor
    return out


class CsvProvider:
    """One file per symbol: <dir>/<SYMBOL>.csv with date,open,high,low,close,volume."""

    def __init__(self, directory):
        self.dir = Path(directory)

    def history(self, tickers: dict[str, str], cutoff: Date | None = None) -> dict[str, pd.DataFrame]:
        out = {}
        for symbol in tickers:
            path = self.dir / f"{symbol}.csv"
            if path.exists():
                out[symbol] = clean(pd.read_csv(path, index_col="date"), cutoff)
        return out

    def fx(self, currencies, base="NOK") -> dict[str, float]:
        path = self.dir / "fx.csv"          # currency,rate
        rates = {base: 1.0}
        if path.exists():
            rates.update(pd.read_csv(path).set_index("currency")["rate"].to_dict())
        return rates


YAHOO_HOSTS = ["https://query1.finance.yahoo.com", "https://query2.finance.yahoo.com"]
YAHOO_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; FinanceBot/1.0)", "Accept": "application/json"}
RETRY_STATUS = {429, 500, 502, 503, 504}


def parse_chart(payload: dict, closes_only: bool = False) -> pd.DataFrame:
    """Turn one Yahoo chart response into our bar format.

    A bar without a close is dropped (Yahoo publishes the day's close with a delay).
    With closes_only (indices, FX) the other price fields may be missing or zero.
    """
    result = payload["chart"]["result"][0]
    stamps = result.get("timestamp") or []
    offset = int((result.get("meta") or {}).get("gmtoffset") or 0)
    quote = result["indicators"]["quote"][0]

    def day(ts) -> Date:
        return datetime.fromtimestamp(int(ts) + offset, tz=ZoneInfo("UTC")).date()

    events = result.get("events") or {}
    divs = {day(e["date"]): float(e["amount"]) for e in (events.get("dividends") or {}).values()
            if e.get("amount")}
    splits = {day(e["date"]): float(e["numerator"]) / float(e["denominator"])
              for e in (events.get("splits") or {}).values()
              if e.get("numerator") and e.get("denominator")}
    rows, index = [], []
    cols = [quote.get(k) or [None] * len(stamps) for k in ("open", "high", "low", "close", "volume")]
    for ts, o, h, l, c, v in zip(stamps, *cols):
        if c is None or c <= 0:
            continue
        if closes_only:
            o, h, l = (x if x else c for x in (o, h, l))
        elif None in (o, h, l) or min(o, h, l) <= 0:
            continue
        d = day(ts)
        index.append(d)
        rows.append({"open": float(o), "high": float(h), "low": float(l), "close": float(c),
                     "volume": float(v or 0), "dividends": divs.get(d, 0.0), "splits": splits.get(d, 0.0)})
    df = pd.DataFrame(rows, index=index, columns=COLUMNS + ["dividends", "splits"])
    return df[~pd.Index(df.index).duplicated(keep="last")].sort_index()


class YahooProvider:
    """Free delayed daily data straight from Yahoo's chart endpoint. Play money only.

    (The yfinance library was dropped: version 1.8 returned the newest bar without a close.)
    """

    def __init__(self, period: str = "2y", workers: int = 4):
        self.period, self.workers = period, workers

    def _get(self, ticker: str) -> dict | None:
        import json, time, urllib.error, urllib.parse, urllib.request
        query = urllib.parse.urlencode({"interval": "1d", "range": self.period, "events": "div,splits"})
        for host in YAHOO_HOSTS:
            url = f"{host}/v8/finance/chart/{urllib.parse.quote(ticker)}?{query}"
            for attempt in range(1, 4):
                try:
                    req = urllib.request.Request(url, headers=YAHOO_HEADERS)
                    with urllib.request.urlopen(req, timeout=20) as resp:
                        return json.loads(resp.read().decode("utf-8"))
                except urllib.error.HTTPError as exc:
                    if exc.code == 404:
                        return None
                    if exc.code not in RETRY_STATUS:
                        break
                except (urllib.error.URLError, TimeoutError, ValueError):
                    pass
                time.sleep(2 ** attempt)
        return None

    def _one(self, ticker: str, cutoff, closes_only: bool) -> pd.DataFrame | None:
        payload = self._get(ticker)
        if payload is None:
            return None
        try:
            df = parse_chart(payload, closes_only)
        except (KeyError, IndexError, TypeError, ValueError):
            return None
        if cutoff:
            df = df[[d <= cutoff for d in df.index]]
        return df if len(df) else None

    def history(self, tickers: dict[str, str], cutoff: Date | None = None,
                closes_only: bool = False) -> dict[str, pd.DataFrame]:
        from concurrent.futures import ThreadPoolExecutor
        out: dict[str, pd.DataFrame] = {}
        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            for symbol, df in zip(tickers, pool.map(lambda t: self._one(t, cutoff, closes_only),
                                                     tickers.values())):
                if df is not None:
                    out[symbol] = df
        missing = [s for s in tickers if s not in out]
        if missing:
            print(f"[data] no bars for {len(missing)}: {', '.join(missing[:25])}"
                  + (" ..." if len(missing) > 25 else ""))
        return out

    def fx(self, currencies, base="NOK") -> dict[str, float]:
        rates = {base: 1.0}
        for ccy in sorted(set(currencies) - {base}):
            df = self._one(f"{ccy}{base}=X", None, True)
            if df is not None and float(df["close"].iloc[-1]) > 0:
                rates[ccy] = float(df["close"].iloc[-1])
            else:
                print(f"[fx] no rate for {ccy}{base}; shares in {ccy} are skipped this run")
        return rates


def liquid_symbols(bars: dict[str, pd.DataFrame], fx_by_symbol: dict[str, float],
                   min_turnover: float = 1e6, min_price: float = 1.0, window: int = 60,
                   min_bars: int = 60, max_daily_move: float = 0.5, move_window: int = 252) -> set[str]:
    """Symbols whose median daily turnover (base currency) is high enough to trade.

    Uses only the bars passed in, so it is as point-in-time as the data itself.
    """
    ok = set()
    for sym, df in bars.items():
        if len(df) < min_bars:
            continue
        # A one-day move beyond +/-50 % is almost always a corporate action (capital
        # distribution, unadjusted split) or a bad tick; price-based signals are meaningless.
        jumps = df["close"].tail(move_window + 1).pct_change().abs()
        if float(jumps.max()) > max_daily_move:
            continue
        tail = df.tail(window)
        turnover = float((tail["close"] * tail["volume"]).median()) * fx_by_symbol.get(sym, 1.0)
        if turnover >= min_turnover and float(tail["close"].iloc[-1]) >= min_price:
            ok.add(sym)
    return ok


def equal_weight_index(bars: dict[str, pd.DataFrame], symbols) -> pd.Series | None:
    """A simple market index built from our own shares: average daily return, compounded."""
    closes = pd.DataFrame({s: total_return_view(bars[s])["close"] for s in symbols if s in bars})
    if closes.shape[1] < 5:
        return None
    daily = closes.pct_change(fill_method=None).clip(-0.5, 0.5).mean(axis=1, skipna=True).fillna(0.0)
    return 100.0 * (1.0 + daily).cumprod()
