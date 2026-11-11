"""Daily bar engine. The same code path is used for the scheduled run and backtests.

For each new completed bar date D:
  1. fill orders queued on the previous bar at D's open (sells first)
  2. trigger stops against D's low (gap-down fills at the open)
  3. mark to market at D's close, record equity
  4. ask the strategy for signals on bars up to D and queue orders for the next open
Decisions never use information that was not available at the time (no look-ahead),
and a date is processed at most once, so re-running the job is harmless.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date as Date
from typing import Optional

import pandas as pd

from app.portfolio import PendingOrder, Portfolio
from app.risk import RiskLimits, daily_loss_breached, size_position
from app.sim_broker import SimBroker
from app.strategy import Context, ScoreBook, TrendStrategy

ORDER_TTL_DAYS = 5      # queued orders expire if the symbol does not trade


@dataclass(frozen=True)
class Instrument:
    symbol: str
    currency: str = "NOK"


def _d(s: str) -> Date:
    return Date.fromisoformat(s)


def run_engine(
    pf: Portfolio,
    bars: dict[str, pd.DataFrame],          # per symbol; index = datetime.date ascending
    instruments: dict[str, Instrument],
    fx: dict[str, float],                   # currency -> base currency rate
    limits: RiskLimits,
    broker: SimBroker,
    strategy=None,
    scores: Optional[ScoreBook] = None,
    start: Optional[Date] = None,
    use_halt: bool = True,
    tradable: Optional[set] = None,     # symbols new buys are allowed in (None = all)
) -> list[dict]:
    """Process all bar dates after pf.last_date (or `start`). Returns equity rows."""
    strategy = strategy or TrendStrategy()
    scores = scores or ScoreBook()
    for ccy in {i.currency for i in instruments.values()}:
        if ccy not in fx:
            raise KeyError(f"No FX rate for {ccy}->{pf.base_currency}")

    all_dates = sorted({d for df in bars.values() for d in df.index})
    if pf.last_date:
        dates = [d for d in all_dates if d > _d(pf.last_date)]
    elif start:
        dates = [d for d in all_dates if d >= start]
    else:
        dates = all_dates[-1:]              # first live run: start from the latest bar

    prev_day = _d(pf.last_date) if pf.last_date else None
    equity_rows: list[dict] = []
    for day in dates:
        iso = day.isoformat()
        today = {s: df.loc[day] for s, df in bars.items() if day in df.index}

        # 1. fill queued orders at the open --------------------------------
        still_pending: list[PendingOrder] = []
        for o in sorted(pf.pending, key=lambda o: o.side != "SELL"):
            bar = today.get(o.symbol)
            if bar is None:
                if (day - _d(o.created)).days <= ORDER_TTL_DAYS:
                    still_pending.append(o)
                continue
            inst = instruments[o.symbol]
            if o.side == "SELL":
                broker.sell_all(o.symbol, float(bar["open"]), fx[inst.currency], iso, o.reason)
            elif o.symbol not in pf.positions and float(bar["open"]) > o.stop:
                broker.buy(o.symbol, o.qty, float(bar["open"]), inst.currency,
                           fx[inst.currency], o.stop, iso, o.reason)
        pf.pending = still_pending

        # 2. stops -----------------------------------------------------------
        for sym in list(pf.positions):
            bar = today.get(sym)
            if bar is None:
                continue
            pos = pf.positions[sym]
            if float(bar["low"]) <= pos.stop:
                fill = min(float(bar["open"]), pos.stop)
                broker.sell_all(sym, fill, fx[pos.currency], iso, "stop hit")

        # 2b. day-trading strategies go flat at the close --------------------
        if getattr(strategy, "intraday", False):
            for sym in list(pf.positions):
                if sym in today:
                    broker.sell_all(sym, float(today[sym]["close"]), fx[pf.positions[sym].currency],
                                    iso, "flat at close")

        # 3. mark to market --------------------------------------------------
        for sym, bar in today.items():
            pf.last_prices[sym] = float(bar["close"])
        equity = pf.equity(fx)
        halted = use_halt and daily_loss_breached(pf.last_equity, equity, limits)
        equity_rows.append({
            "date": iso, "equity": round(equity, 2), "cash": round(pf.cash, 2),
            "positions_value": round(equity - pf.cash, 2),
            "n_positions": len(pf.positions), "halted": int(halted),
        })

        # 4. signals for the next open ---------------------------------------
        queued = {o.symbol for o in pf.pending}
        hist = {s: bars[s].loc[:day] for s in today            # only symbols with a fresh bar
                if s in instruments and s not in queued}
        if tradable is not None:                # held positions are still managed and sold
            hist = {s: h for s, h in hist.items() if s in tradable or s in pf.positions}
        n_universe = len(instruments) if tradable is None else len(tradable)
        ctx = Context(day, prev_day, hist, pf, scores, n_universe)
        candidates = []
        for sym, sig in strategy.signals(ctx).items():
            if sym not in hist:
                continue
            if sym in pf.positions:
                if sig.action == "SELL":
                    pf.pending.append(PendingOrder(sym, "SELL", pf.positions[sym].qty, iso,
                                                   reason=sig.reason))
                elif sig.stop > pf.positions[sym].stop:
                    pf.positions[sym].stop = round(sig.stop, 4)   # stops only move up
            elif sig.action == "BUY" and (tradable is None or sym in tradable):
                candidates.append((sig, sym, float(today[sym]["close"])))

        if not halted:
            open_risk = pf.open_risk(fx)
            n_open = len(pf.positions)
            cash = pf.cash
            for sig, sym, close in sorted(candidates, key=lambda c: -c[0].score):
                rate = fx[instruments[sym].currency]
                if sig.weight > 0:
                    # weight-based models: cash is re-checked at the fill, after sells
                    qty = equity * sig.weight / (close * rate * 1.003)
                    qty = round(qty, 4) if getattr(strategy, "fractional", False) else int(qty)
                    if qty > 0:
                        pf.pending.append(PendingOrder(sym, "BUY", qty, iso, round(sig.stop, 4),
                                                       sig.reason))
                    continue
                size = size_position(
                    equity=equity, cash=cash, price=close, stop=sig.stop, fx=rate,
                    open_risk=open_risk, n_open=n_open, limits=limits,
                )
                if size.qty <= 0:
                    continue
                pf.pending.append(PendingOrder(sym, "BUY", size.qty, iso, round(sig.stop, 4),
                                               f"{sig.reason}; {size.reason}"))
                open_risk += (close - sig.stop) * size.qty * rate
                cash -= close * size.qty * rate
                n_open += 1

        prev_day = day
        pf.last_date, pf.last_equity = iso, equity

    return equity_rows
