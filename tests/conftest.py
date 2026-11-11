import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def make_bars(closes, start=date(2025, 1, 1), spread=0.01, opens=None, lows=None):
    """Daily bars from a list of closes; open = previous close unless given."""
    days, d = [], start
    while len(days) < len(closes):
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    rows = []
    for i, c in enumerate(closes):
        o = opens[i] if opens and opens[i] is not None else (closes[i - 1] if i else c)
        lo = lows[i] if lows and lows[i] is not None else min(o, c) * (1 - spread)
        rows.append({"open": o, "high": max(o, c) * (1 + spread), "low": lo, "close": c, "volume": 1000})
    return pd.DataFrame(rows, index=days)


@pytest.fixture
def uptrend():
    return make_bars([100 * 1.004 ** i for i in range(120)])
