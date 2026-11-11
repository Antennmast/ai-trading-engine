"""Strategies turn completed bars into signals. No orders, no state, no I/O.

The "AI" plugs in through ScoreBook: any model (sentiment score, LLM, ML) only has
to write rows of symbol,date,score (-1..1) to signals/scores.csv. The engine never
lets a score bypass the risk limits.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date as Date
from typing import Optional

import pandas as pd


@dataclass(frozen=True)
class Signal:
    action: str                 # "BUY" | "SELL" | "HOLD"
    stop: float = 0.0           # proposed (trailing) stop, local currency; 0 = no stop
    score: float = 0.0          # ranking among BUY candidates
    reason: str = ""
    weight: float = 0.0         # >0: buy this share of equity instead of risk-based sizing


class ScoreBook:
    """External scores per symbol/date. Stale scores are ignored."""

    def __init__(self, rows: Optional[pd.DataFrame] = None, max_age_days: int = 5):
        self.max_age_days = max_age_days
        self._by_symbol: dict[str, pd.Series] = {}
        if rows is not None and len(rows):
            rows = rows.copy()
            rows["date"] = pd.to_datetime(rows["date"]).dt.date
            for sym, grp in rows.groupby("symbol"):
                self._by_symbol[str(sym)] = grp.sort_values("date").set_index("date")["score"]

    @classmethod
    def from_csv(cls, path, max_age_days: int = 5) -> "ScoreBook":
        try:
            return cls(pd.read_csv(path, dtype={"symbol": str}), max_age_days)
        except (FileNotFoundError, pd.errors.EmptyDataError):
            return cls(None, max_age_days)

    def get(self, symbol: str, asof: Date) -> Optional[float]:
        s = self._by_symbol.get(symbol)
        if s is None:
            return None
        s = s[[d <= asof for d in s.index]]       # never look into the future
        if s.empty or (asof - s.index[-1]).days > self.max_age_days:
            return None
        return float(s.iloc[-1])


def atr(bars: pd.DataFrame, n: int = 14) -> float:
    prev_close = bars["close"].shift(1)
    tr = pd.concat([
        bars["high"] - bars["low"],
        (bars["high"] - prev_close).abs(),
        (bars["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    return float(tr.rolling(n).mean().iloc[-1])


@dataclass
class TrendStrategy:
    """Baseline swing rule: trade with the trend, ATR-based trailing stop.

    BUY  : close > SMA(fast) > SMA(slow)   (and score >= buy_score if a score exists)
    SELL : close < SMA(slow)               (or score <= sell_score)
    This is a placeholder to exercise the plumbing, not a validated edge.
    """
    fast: int = 20
    slow: int = 50
    atr_n: int = 14
    atr_mult: float = 2.5
    buy_score: float = 0.2
    sell_score: float = -0.5
    require_score: bool = False   # True: never buy without a fresh score

    intraday = False

    def signals(self, ctx: "Context") -> dict[str, Signal]:
        return {sym: self.decide(hist, ctx.pf.positions.get(sym), ctx.scores.get(sym, ctx.day))
                for sym, hist in ctx.hist.items()}

    def decide(self, bars: pd.DataFrame, has_position,
               score: Optional[float] = None) -> Signal:
        if len(bars) < self.slow + 1:
            return Signal("HOLD", reason="not enough history")
        close = float(bars["close"].iloc[-1])
        sma_f = float(bars["close"].rolling(self.fast).mean().iloc[-1])
        sma_s = float(bars["close"].rolling(self.slow).mean().iloc[-1])
        a = atr(bars, self.atr_n)
        if not a or a != a:                       # zero or NaN
            return Signal("HOLD", reason="no ATR")
        stop = close - self.atr_mult * a

        if has_position:
            if close < sma_s:
                return Signal("SELL", reason="close below slow SMA")
            if score is not None and score <= self.sell_score:
                return Signal("SELL", reason=f"score {score:+.2f}")
            return Signal("HOLD", stop=stop, reason="trail stop")

        if close > sma_f > sma_s:
            if score is None and self.require_score:
                return Signal("HOLD", reason="no fresh score")
            if score is not None and score < self.buy_score:
                return Signal("HOLD", reason=f"score {score:+.2f} too low")
            rank = (close / sma_s - 1.0) + (score or 0.0)
            why = "uptrend" + (f", score {score:+.2f}" if score is not None else "")
            return Signal("BUY", stop=stop, score=rank, reason=why)
        return Signal("HOLD", reason="no trend")


# ---------------------------------------------------------------------------
# Portfolio-level interface: every strategy implements signals(ctx).
# ---------------------------------------------------------------------------
@dataclass
class Context:
    day: Date
    prev_day: Optional[Date]            # previous bar date seen by the engine
    hist: dict                          # symbol -> bars up to and including `day`
    pf: object                          # Portfolio (read-only by convention)
    scores: ScoreBook
    universe_size: int = 0


def rsi(close: pd.Series, n: int = 2) -> float:
    delta = close.diff()
    up = delta.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    down = (-delta.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    if down.iloc[-1] == 0:
        return 100.0
    return float(100 - 100 / (1 + up.iloc[-1] / down.iloc[-1]))


@dataclass
class BuyAndHold:
    """Benchmark: equal weight in every instrument, bought once, never sold."""
    intraday = False

    def signals(self, ctx: Context) -> dict[str, Signal]:
        w = 1.0 / max(ctx.universe_size, 1)
        return {sym: Signal("BUY", weight=w, reason="benchmark equal weight")
                for sym in ctx.hist if sym not in ctx.pf.positions}


@dataclass
class MomentumRotation:
    """Hold the top_n instruments by lookback return; re-rank on the first bar of a month."""
    lookback: int = 126
    top_n: int = 4
    intraday = False

    def signals(self, ctx: Context) -> dict[str, Signal]:
        new_month = ctx.prev_day is not None and ctx.prev_day.month != ctx.day.month
        empty = not ctx.pf.positions and not ctx.pf.pending
        if not (new_month or empty):
            return {}
        ret = {s: float(h["close"].iloc[-1] / h["close"].iloc[-1 - self.lookback] - 1)
               for s, h in ctx.hist.items() if len(h) > self.lookback}
        winners = sorted(ret, key=ret.get, reverse=True)[: self.top_n]
        out = {s: Signal("SELL", reason="dropped out of top ranks")
               for s in ctx.pf.positions if s in ctx.hist and s not in winners}
        for s in winners:
            if s not in ctx.pf.positions:
                out[s] = Signal("BUY", weight=1.0 / self.top_n, score=ret[s],
                                reason=f"top {self.top_n} momentum ({ret[s]:+.0%})")
        return out


@dataclass
class MeanReversion:
    """Buy short, sharp dips in instruments that are in a longer uptrend."""
    trend: int = 100
    rsi_n: int = 2
    rsi_buy: float = 10.0
    exit_sma: int = 5
    max_days: int = 7
    atr_mult: float = 3.0
    intraday = False

    def signals(self, ctx: Context) -> dict[str, Signal]:
        out = {}
        for sym, h in ctx.hist.items():
            if len(h) < self.trend + 1:
                continue
            close = float(h["close"].iloc[-1])
            pos = ctx.pf.positions.get(sym)
            if pos:
                held = (ctx.day - Date.fromisoformat(pos.entry_date)).days
                if close > float(h["close"].rolling(self.exit_sma).mean().iloc[-1]):
                    out[sym] = Signal("SELL", reason="bounce: close above 5-day average")
                elif held >= self.max_days:
                    out[sym] = Signal("SELL", reason="time exit")
                continue
            r = rsi(h["close"], self.rsi_n)
            a = atr(h)
            if close > float(h["close"].rolling(self.trend).mean().iloc[-1]) and r < self.rsi_buy and a == a and a > 0:
                out[sym] = Signal("BUY", stop=close - self.atr_mult * a, score=-r,
                                  reason=f"dip in uptrend (RSI {r:.0f})")
        return out


@dataclass
class OpenToClose:
    """Day trading with daily bars: buy at the open, sell everything at the same close.

    Picks instruments in an uptrend with the strongest 5-day move. Exists mainly to
    measure what daily turnover costs.
    """
    top_n: int = 5
    fast: int = 20
    slow: int = 50
    intraday = True

    def signals(self, ctx: Context) -> dict[str, Signal]:
        cands = {}
        for sym, h in ctx.hist.items():
            if len(h) < self.slow + 1:
                continue
            c = h["close"]
            if float(c.iloc[-1]) > float(c.rolling(self.fast).mean().iloc[-1]) > float(c.rolling(self.slow).mean().iloc[-1]):
                cands[sym] = float(c.iloc[-1] / c.iloc[-6] - 1)
        picks = sorted(cands, key=cands.get, reverse=True)[: self.top_n]
        w = 0.95 / self.top_n
        return {s: Signal("BUY", weight=w, score=cands[s], reason="day trade: uptrend") for s in picks}
