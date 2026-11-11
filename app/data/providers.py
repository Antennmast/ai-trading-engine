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


class YahooProvider:
    """Free delayed daily data via yfinance (unofficial). Fine for play money only."""

    def __init__(self, period: str = "1y"):
        self.period = period

    def history(self, tickers: dict[str, str], cutoff: Date | None = None,
                chunk: int = 60) -> dict[str, pd.DataFrame]:
        import yfinance as yf
        out: dict[str, pd.DataFrame] = {}
        items = list(tickers.items())
        for i in range(0, len(items), chunk):
            part = items[i:i + chunk]
            try:
                # auto_adjust=False: raw prices, so stops/fills match what actually traded
                raw = yf.download([t for _, t in part], period=self.period, interval="1d",
                                  auto_adjust=False, actions=True, group_by="ticker",
                                  threads=True, progress=False)
            except Exception as exc:                # one bad batch must not stop the run
                print(f"[data] batch {i // chunk} failed: {exc}")
                continue
            for symbol, ticker in part:
                try:
                    df = clean(raw[ticker], cutoff)
                except Exception:
                    continue
                if len(df):
                    out[symbol] = df
        # The threaded batch download occasionally drops a ticker (cache lock, timeout).
        # Retry those one by one so a held share does not silently miss a day.
        for symbol in [s for s in tickers if s not in out][:40]:
            try:
                raw = yf.Ticker(tickers[symbol]).history(period=self.period, interval="1d",
                                                         auto_adjust=False, actions=True)
                df = clean(raw, cutoff)
                if len(df):
                    out[symbol] = df
            except Exception:
                pass
        missing = [s for s in tickers if s not in out]
        if missing:
            print(f"[data] no bars for {len(missing)}: {', '.join(missing[:25])}"
                  + (" ..." if len(missing) > 25 else ""))
        return out

    def fx(self, currencies, base="NOK") -> dict[str, float]:
        import yfinance as yf
        rates = {base: 1.0}
        for ccy in sorted(set(currencies) - {base}):
            try:
                px = yf.Ticker(f"{ccy}{base}=X").history(period="5d")["Close"].dropna()
                if not px.empty and float(px.iloc[-1]) > 0:
                    rates[ccy] = float(px.iloc[-1])
                    continue
            except Exception as exc:
                print(f"[fx] {ccy}{base} failed: {exc}")
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
