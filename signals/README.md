# External scores (the "AI" input)

Any model can steer the engine by appending rows to `scores.csv`:

    symbol,date,score
    EQNR,2026-11-10,0.45

- `score` is -1 (very negative) .. +1 (very positive); `symbol` must match `config/universe.csv`.
- `date` is the day the score was known. The engine only uses scores dated on or before
  the bar it is deciding on, and ignores scores older than 5 days.
- A score can veto a buy (< 0.2) or force an exit (<= -0.5). It can never override
  position sizing, the open-risk cap or the daily loss halt.
- Set `REQUIRE_SCORE=true` to forbid buys without a fresh score.
