"""Print the tail of Yahoo data for a few tickers (diagnostics only)."""
import sys
from datetime import datetime, timezone

from app.data.providers import YahooProvider

tickers = sys.argv[1:] or ["EQNR.OL", "DNB.OL", "VOLV-B.ST", "NOVO-B.CO", "NOKIA.HE"]
print("now utc", datetime.now(timezone.utc).isoformat())
p = YahooProvider(period="1mo")
for name, df in p.history({t: t for t in tickers}).items():
    print("==", name); print(df.tail(3).to_string())
for name, df in p.history({"OSEBX.OL": "OSEBX.OL", "^OMX": "^OMX"}, closes_only=True).items():
    print("== index", name); print(df["close"].tail(3).to_string())
print("fx", p.fx({"SEK", "DKK", "EUR"}))
