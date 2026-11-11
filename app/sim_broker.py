"""Simulated broker: fills against daily bars with slippage and commission."""
from __future__ import annotations

from dataclasses import dataclass

from app.portfolio import Portfolio, Position


@dataclass(frozen=True)
class Costs:
    commission_rate: float = 0.0005     # 5 bps of traded value
    min_commission: float = 10.0        # base currency per order
    slippage_bps: float = 5.0           # adverse price move on every fill

    def commission(self, value_base: float) -> float:
        return max(value_base * self.commission_rate, self.min_commission)


class SimBroker:
    def __init__(self, portfolio: Portfolio, costs: Costs | None = None):
        self.pf = portfolio
        self.costs = costs or Costs()
        self.trades: list[dict] = []     # new fills this run

    def buy(self, symbol, qty, price, currency, fx, stop, date, reason="") -> int:
        """Buy up to qty at price (local ccy). Shrinks qty to fit cash. Returns filled qty."""
        px = price * (1 + self.costs.slippage_bps / 1e4)
        if float(qty).is_integer():
            while qty > 0:
                value = qty * px * fx
                fee = self.costs.commission(value)
                if value + fee <= self.pf.cash:
                    break
                qty -= 1
        else:                                   # fractional shares (benchmark only)
            fee = self.costs.commission(qty * px * fx)
            qty = round(min(qty, max(self.pf.cash - fee, 0.0) / (px * fx)), 4)
            value = qty * px * fx
            fee = self.costs.commission(value)
            if value + fee > self.pf.cash:
                qty = 0
        if qty <= 0:
            return 0
        self.pf.cash -= value + fee
        old = self.pf.positions.get(symbol)
        if old:
            total = old.qty + qty
            old.avg_price = (old.avg_price * old.qty + px * qty) / total
            old.qty = total
            old.stop = max(old.stop, stop)
        else:
            self.pf.positions[symbol] = Position(symbol, qty, px, currency, stop, date)
        self.pf.last_prices[symbol] = price
        self._log(date, symbol, "BUY", qty, px, currency, fx, fee, 0.0, reason)
        return qty

    def sell_all(self, symbol, price, fx, date, reason="") -> int:
        pos = self.pf.positions.pop(symbol, None)
        if not pos:
            return 0
        px = price * (1 - self.costs.slippage_bps / 1e4)
        value = pos.qty * px * fx
        fee = self.costs.commission(value)
        self.pf.cash += value - fee
        pnl = (px - pos.avg_price) * pos.qty * fx - fee   # entry fee already paid from cash
        self._log(date, symbol, "SELL", pos.qty, px, pos.currency, fx, fee, pnl, reason)
        return pos.qty

    def _log(self, date, symbol, side, qty, px, ccy, fx, fee, pnl, reason):
        self.trades.append({
            "date": date, "symbol": symbol, "side": side, "qty": qty,
            "price": round(px, 4), "currency": ccy, "fx": round(fx, 6),
            "fee": round(fee, 2), "pnl": round(pnl, 2), "reason": reason,
        })
