"""Raw look at Yahoo's chart response for the newest bar (diagnostics only)."""
import json, sys, time, urllib.parse, urllib.request
from datetime import datetime, timezone

from app.data.providers import YAHOO_HEADERS

def get(ticker, **params):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(ticker)}?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(urllib.request.Request(url, headers=YAHOO_HEADERS), timeout=20) as r:
        return json.loads(r.read())["chart"]["result"][0]

now = int(time.time())
print("now utc", datetime.now(timezone.utc).isoformat())
for t in sys.argv[1:] or ["EQNR.OL", "VOLV-B.ST", "OSEBX.OL"]:
    for label, params in [("range=5d", dict(interval="1d", range="5d")),
                          ("range=2y", dict(interval="1d", range="2y", events="div,splits")),
                          ("period", dict(interval="1d", period1=now - 6 * 86400, period2=now)),
                          ("1h", dict(interval="1h", range="2d"))]:
        try:
            r = get(t, **params)
        except Exception as exc:
            print(t, label, "ERR", exc); continue
        m, q = r["meta"], r["indicators"]["quote"][0]
        ts = r.get("timestamp") or []
        tail = [(datetime.fromtimestamp(x, timezone.utc).strftime("%m-%d %H:%M"), o, c)
                for x, o, c in zip(ts[-3:], q["open"][-3:], q["close"][-3:])]
        print(t, label, "| price", m.get("regularMarketPrice"), "at",
              datetime.fromtimestamp(m.get("regularMarketTime", 0), timezone.utc).strftime("%m-%d %H:%M"),
              "| prevClose", m.get("chartPreviousClose"), "| tail(ts,open,close)", tail)
        adj = (r["indicators"].get("adjclose") or [{}])[0].get("adjclose")
        if adj: print("   adjclose tail", adj[-2:])
