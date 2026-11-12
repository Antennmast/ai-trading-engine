"""Print the raw tail of Yahoo data for a few tickers (diagnostics only)."""
import sys
from datetime import datetime, timezone

import pandas as pd
import yfinance as yf

pd.set_option("display.width", 200)
tickers = sys.argv[1:] or ["EQNR.OL", "DNB.OL", "VOLV-B.ST", "NOVO-B.CO", "NOKIA.HE", "OSEBX.OL", "^OMXC25"]
print("now utc", datetime.now(timezone.utc).isoformat(), "yfinance", yf.__version__)
raw = yf.download(tickers, period="1mo", interval="1d", auto_adjust=False, actions=True,
                  group_by="ticker", threads=True, progress=False)
print("batch index tail:", list(raw.index[-4:]))
for t in tickers:
    print("== batch", t); print(raw[t].tail(3).to_string())
for t in tickers[:3]:
    h = yf.Ticker(t).history(period="1mo", interval="1d", auto_adjust=False, actions=True)
    print("== single", t); print(h.tail(3).to_string())
