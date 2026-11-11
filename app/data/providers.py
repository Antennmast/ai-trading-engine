"""Price data providers. Each returns daily OHLCV with a datetime.date index."""
from __future__ import annotations

from datetime import date as Date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

COLUMNS = ["open", "high", "low", "close", "volume"]
MARKET_TZ = ZoneInfo("Europe/Oslo")
BAR_FINAL_AFTER = time(17, 15)      # Nordic closes ~16:25-17:30 local; Yahoo is delayed


def last_completed_bar_date(now: datetime | None = None) -> Date:
    """Today's bar only counts once the session is over; otherwise use up to yesterday."""
    now = (now or datetime.now(MARKET_TZ)).astimezone(MARKET_TZ)
    if now.time() >= BAR_FINAL_AFTER:
        return now.date()
    return (now - pd.Timedelta(days=1)).date()


def clean(df: pd.DataFrame, cutoff: Date | None = None) -> pd.DataFrame:
    df = df.rename(columns=str.lower)[COLUMNS].dropna(subset=["open", "high", "low", "close"])
    df.index = pd.to_datetime(df.index).date
    df = df[~pd.Index(df.index).duplicated(keep="last")].sort_index()
    df = df[(df[["open", "high", "low", "close"]] > 0).all(axis=1)]
    if cutoff:
        df = df[[d <= cutoff for d in df.index]]
    return df


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


class YahooProvider:
    """Free delayed daily data via yfinance (unofficial). Fine for play money only."""

    def __init__(self, period: str = "1y"):
        self.period = period

    def history(self, tickers: dict[str, str], cutoff: Date | None = None) -> dict[str, pd.DataFrame]:
        import yfinance as yf
        out = {}
        for symbol, ticker in tickers.items():
            try:
                # auto_adjust=False: raw prices, so stops/fills match what actually traded
                raw = yf.Ticker(ticker).history(period=self.period, interval="1d", auto_adjust=False)
                df = clean(raw, cutoff)
            except Exception as exc:                # one bad ticker must not stop the run
                print(f"[data] {symbol} ({ticker}) failed: {exc}")
                continue
            if len(df):
                out[symbol] = df
            else:
                print(f"[data] {symbol} ({ticker}): no bars")
        return out

    def fx(self, currencies, base="NOK") -> dict[str, float]:
        import yfinance as yf
        rates = {base: 1.0}
        for ccy in set(currencies) - {base}:
            px = yf.Ticker(f"{ccy}{base}=X").history(period="5d")["Close"].dropna()
            if px.empty:
                raise RuntimeError(f"No FX rate for {ccy}{base}")
            rates[ccy] = float(px.iloc[-1])
        return rates
