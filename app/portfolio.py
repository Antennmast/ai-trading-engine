"""Portfolio state for the simulated (play-money) broker. Persisted as JSON."""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class Position:
    symbol: str
    qty: int
    avg_price: float          # local currency, incl. slippage
    currency: str
    stop: float               # local currency
    entry_date: str


@dataclass
class PendingOrder:
    symbol: str
    side: str                 # "BUY" | "SELL"
    qty: int
    created: str              # date of the bar the decision was made on
    stop: float = 0.0
    reason: str = ""


@dataclass
class Portfolio:
    cash: float                               # base currency (NOK)
    base_currency: str = "NOK"
    positions: dict[str, Position] = field(default_factory=dict)
    pending: list[PendingOrder] = field(default_factory=list)
    last_date: Optional[str] = None           # last bar date fully processed
    last_equity: Optional[float] = None       # equity at close of last_date
    last_prices: dict[str, float] = field(default_factory=dict)  # local ccy

    # ---- persistence -------------------------------------------------
    @classmethod
    def load(cls, path: Path, starting_cash: float, base_currency: str = "NOK") -> "Portfolio":
        if not Path(path).exists():
            return cls(cash=starting_cash, base_currency=base_currency)
        raw = json.loads(Path(path).read_text())
        return cls(
            cash=raw["cash"],
            base_currency=raw.get("base_currency", base_currency),
            positions={s: Position(**p) for s, p in raw.get("positions", {}).items()},
            pending=[PendingOrder(**o) for o in raw.get("pending", [])],
            last_date=raw.get("last_date"),
            last_equity=raw.get("last_equity"),
            last_prices=raw.get("last_prices", {}),
        )

    def save(self, path: Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(asdict(self), indent=2, sort_keys=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
        with os.fdopen(fd, "w") as fh:        # atomic replace: never a half-written state
            fh.write(payload + "\n")
        os.replace(tmp, path)

    # ---- valuation ---------------------------------------------------
    def positions_value(self, fx: dict[str, float]) -> float:
        return sum(
            p.qty * self.last_prices.get(s, p.avg_price) * fx[p.currency]
            for s, p in self.positions.items()
        )

    def equity(self, fx: dict[str, float]) -> float:
        return self.cash + self.positions_value(fx)

    def open_risk(self, fx: dict[str, float]) -> float:
        """Loss in base currency if every position were stopped out at its stop."""
        risk = 0.0
        for s, p in self.positions.items():
            if p.stop <= 0:
                continue                      # weight-based models carry no stop
            px = self.last_prices.get(s, p.avg_price)
            risk += max(px - p.stop, 0.0) * p.qty * fx[p.currency]
        return risk
