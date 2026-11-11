"""Position sizing and portfolio-level risk limits. Pure functions, no I/O."""
from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class RiskLimits:
    risk_per_trade: float = 0.005
    max_open_risk: float = 0.02
    max_positions: int = 5
    max_position_weight: float = 0.20
    daily_loss_limit: float = 0.01


@dataclass(frozen=True)
class SizeResult:
    qty: int
    reason: str


def size_position(
    *,
    equity: float,
    cash: float,
    price: float,
    stop: float,
    fx: float,
    open_risk: float,
    n_open: int,
    limits: RiskLimits,
    cost_buffer: float = 0.003,
) -> SizeResult:
    """Number of shares to buy. All money amounts in base currency; price/stop local.

    qty is the minimum of four caps: per-trade risk, remaining aggregate open risk,
    max position weight and available cash. Returns qty=0 with a reason when blocked.
    """
    if price <= 0 or fx <= 0 or equity <= 0:
        return SizeResult(0, "invalid price/fx/equity")
    if not (0 < stop < price):
        return SizeResult(0, "stop must be below price (long-only)")
    if n_open >= limits.max_positions:
        return SizeResult(0, "max positions reached")

    risk_per_share = (price - stop) * fx
    value_per_share = price * fx

    by_trade_risk = equity * limits.risk_per_trade / risk_per_share
    remaining_risk = equity * limits.max_open_risk - open_risk
    if remaining_risk <= 0:
        return SizeResult(0, "aggregate open-risk limit reached")
    by_open_risk = remaining_risk / risk_per_share
    by_weight = equity * limits.max_position_weight / value_per_share
    by_cash = cash / (value_per_share * (1 + cost_buffer))

    caps = {
        "risk per trade": by_trade_risk,
        "open-risk limit": by_open_risk,
        "max position weight": by_weight,
        "cash": by_cash,
    }
    binding = min(caps, key=caps.get)
    qty = max(math.floor(caps[binding]), 0)
    if qty == 0:
        return SizeResult(0, f"size rounds to zero ({binding})")
    return SizeResult(qty, f"sized by {binding}")


def daily_loss_breached(prev_equity: float | None, equity: float, limits: RiskLimits) -> bool:
    if not prev_equity or prev_equity <= 0:
        return False
    return (equity / prev_equity - 1.0) <= -limits.daily_loss_limit
