from datetime import date

import pandas as pd
import pytest

from app.engine import Instrument, run_engine
from app.portfolio import PendingOrder, Portfolio, Position
from app.risk import RiskLimits
from app.sim_broker import Costs, SimBroker
from app.strategy import ScoreBook, TrendStrategy
from tests.conftest import make_bars

FX = {"NOK": 1.0}
INST = {"AAA": Instrument("AAA", "NOK")}
NO_COST = Costs(commission_rate=0, min_commission=0, slippage_bps=0)


def run(pf, bars, costs=NO_COST, inst=INST, fx=FX, **kw):
    broker = SimBroker(pf, costs)
    rows = run_engine(pf, bars, inst, fx, RiskLimits(), broker, **kw)
    return broker, rows


def test_buy_fills_at_next_open_not_signal_close(uptrend):
    pf = Portfolio(cash=100_000)
    broker, _ = run(pf, {"AAA": uptrend}, start=uptrend.index[0])
    buy = broker.trades[0]
    signal_day = uptrend.index[50]                 # first day with 51 bars of history
    fill_day = uptrend.index[51]
    assert buy["side"] == "BUY" and buy["date"] == fill_day.isoformat()
    assert buy["price"] == pytest.approx(uptrend.loc[fill_day, "open"], abs=1e-4)
    assert buy["price"] == pytest.approx(uptrend.loc[signal_day, "close"], abs=1e-4)


def test_rerun_same_data_is_a_noop(uptrend):
    pf = Portfolio(cash=100_000)
    run(pf, {"AAA": uptrend}, start=uptrend.index[0])
    cash, qty = pf.cash, pf.positions["AAA"].qty
    broker, rows = run(pf, {"AAA": uptrend})
    assert rows == [] and broker.trades == []
    assert (pf.cash, pf.positions["AAA"].qty) == (cash, qty)


def test_incremental_runs_match_one_big_run(uptrend):
    full = Portfolio(cash=100_000)
    run(full, {"AAA": uptrend}, start=uptrend.index[0])
    step = Portfolio(cash=100_000)
    run(step, {"AAA": uptrend.iloc[:60]}, start=uptrend.index[0])
    for n in range(61, len(uptrend) + 1):          # one new bar per "day"
        run(step, {"AAA": uptrend.iloc[:n]})
    assert step.cash == pytest.approx(full.cash)
    assert step.positions["AAA"].qty == full.positions["AAA"].qty
    assert step.positions["AAA"].stop == pytest.approx(full.positions["AAA"].stop)


def test_first_live_run_only_processes_latest_bar(uptrend):
    pf = Portfolio(cash=100_000)
    _, rows = run(pf, {"AAA": uptrend})
    assert len(rows) == 1 and pf.last_date == uptrend.index[-1].isoformat()
    assert pf.positions == {} and len(pf.pending) == 1     # queued, not filled


def test_stop_fills_at_stop_and_gap_fills_at_open():
    bars = make_bars([100, 100, 100], lows=[None, 94, None])
    pf = Portfolio(cash=0, last_date=bars.index[0].isoformat(),
                   positions={"AAA": Position("AAA", 10, 100, "NOK", 95, "2024-12-31")})
    broker, _ = run(pf, {"AAA": bars})
    assert broker.trades[0]["price"] == 95 and broker.trades[0]["reason"] == "stop hit"
    assert pf.cash == 950 and "AAA" not in pf.positions

    gap = make_bars([100, 90, 90], opens=[None, 90, None])
    pf = Portfolio(cash=0, last_date=gap.index[0].isoformat(),
                   positions={"AAA": Position("AAA", 10, 100, "NOK", 95, "2024-12-31")})
    broker, _ = run(pf, {"AAA": gap})
    assert broker.trades[0]["price"] == 90         # gapped through the stop: no fantasy fill at 95


def test_stop_only_moves_up(uptrend):
    pf = Portfolio(cash=100_000)
    run(pf, {"AAA": uptrend.iloc[:80]}, start=uptrend.index[0])
    before = pf.positions["AAA"].stop
    run(pf, {"AAA": uptrend})
    assert pf.positions["AAA"].stop > before


def test_limits_hold_across_many_candidates():
    bars = {f"S{i}": make_bars([100 * 1.004 ** k for k in range(120)]) for i in range(8)}
    inst = {s: Instrument(s, "NOK") for s in bars}
    pf = Portfolio(cash=100_000)
    _, rows = run(pf, bars, inst=inst, start=date(2025, 1, 1))
    lim = RiskLimits()
    assert 0 < len(pf.positions) <= lim.max_positions
    eq = pf.equity(FX)
    for p in pf.positions.values():
        assert p.qty * p.avg_price <= eq * lim.max_position_weight * 1.02
    assert pf.cash >= 0
    assert all(r["cash"] >= 0 for r in rows)


def test_daily_loss_halt_blocks_new_buys(uptrend):
    crash = make_bars([100, 80])                   # held position drops 20 %
    crash.index = list(uptrend.index[-2:])
    pf = Portfolio(cash=50_000, last_date=uptrend.index[-3].isoformat(), last_equity=100_000,
                   positions={"BBB": Position("BBB", 500, 100, "NOK", 1, "2024-12-31")})
    inst = {"AAA": Instrument("AAA"), "BBB": Instrument("BBB")}
    _, rows = run(pf, {"AAA": uptrend, "BBB": crash}, inst=inst)
    assert rows[-1]["halted"] == 1
    assert not [o for o in pf.pending if o.side == "BUY" and o.created == rows[-1]["date"]]


def test_missing_fx_rate_is_an_error(uptrend):
    with pytest.raises(KeyError):
        run(Portfolio(cash=1), {"AAA": uptrend}, inst={"AAA": Instrument("AAA", "SEK")})


def test_foreign_currency_cash_accounting():
    pf = Portfolio(cash=100_000)
    b = SimBroker(pf, NO_COST)
    assert b.buy("X", 10, 50.0, "EUR", 11.5, 45.0, "2025-01-02") == 10
    assert pf.cash == pytest.approx(100_000 - 10 * 50 * 11.5)
    pf.last_prices["X"] = 55.0
    assert pf.equity({"NOK": 1, "EUR": 11.5}) == pytest.approx(100_000 + 10 * 5 * 11.5)
    assert pf.open_risk({"NOK": 1, "EUR": 11.5}) == pytest.approx(10 * 10 * 11.5)


def test_buy_shrinks_to_cash_and_costs_reduce_pnl():
    pf = Portfolio(cash=1_000)
    b = SimBroker(pf, Costs(commission_rate=0.001, min_commission=10, slippage_bps=10))
    assert b.buy("X", 50, 100.0, "NOK", 1.0, 90.0, "2025-01-02") == 9   # 9*100.1+10 <= 1000
    b.sell_all("X", 100.0, 1.0, "2025-01-03")
    assert pf.cash < 1_000                         # round trip at flat price loses money
    assert b.trades[-1]["pnl"] < 0


def test_stale_pending_order_expires():
    bars = make_bars([100] * 12)
    pf = Portfolio(cash=10_000, last_date=bars.index[0].isoformat(),
                   pending=[PendingOrder("ZZZ", "BUY", 1, bars.index[0].isoformat(), 90)])
    run(pf, {"AAA": bars}, inst={**INST, "ZZZ": Instrument("ZZZ")})
    assert pf.pending == []


def test_scores_veto_force_exit_and_staleness(uptrend):
    s = TrendStrategy()
    assert s.decide(uptrend, False).action == "BUY"
    assert s.decide(uptrend, False, score=-0.1).action == "HOLD"
    assert s.decide(uptrend, False, score=0.6).action == "BUY"
    assert s.decide(uptrend, True, score=-0.8).action == "SELL"
    assert TrendStrategy(require_score=True).decide(uptrend, False).action == "HOLD"

    book = ScoreBook(pd.DataFrame({"symbol": ["A", "A"], "date": ["2025-03-03", "2025-03-10"],
                                   "score": [0.1, 0.9]}))
    assert book.get("A", date(2025, 3, 5)) == 0.1          # the 03-10 score is in the future
    assert book.get("A", date(2025, 3, 11)) == 0.9
    assert book.get("A", date(2025, 3, 20)) is None        # older than 5 days
    assert book.get("B", date(2025, 3, 5)) is None


def test_portfolio_roundtrip(tmp_path):
    pf = Portfolio(cash=123.45, last_date="2025-01-02", last_equity=500.0,
                   positions={"A": Position("A", 3, 10.0, "NOK", 9.0, "2025-01-02")},
                   pending=[PendingOrder("B", "BUY", 2, "2025-01-02", 8.0, "x")],
                   last_prices={"A": 11.0})
    pf.save(tmp_path / "s" / "portfolio.json")
    assert Portfolio.load(tmp_path / "s" / "portfolio.json", 0) == pf
    assert Portfolio.load(tmp_path / "missing.json", 777).cash == 777


# ---- the other models ------------------------------------------------------
from app.models import MODELS
from app.strategy import BuyAndHold, MeanReversion, MomentumRotation, OpenToClose


def _many(n=6, days=200, drift=None):
    drift = drift or [0.001 * (i - 2) for i in range(n)]
    bars = {f"S{i}": make_bars([100 * (1 + drift[i]) ** k for k in range(days)]) for i in range(n)}
    return bars, {s: Instrument(s, "NOK") for s in bars}


def test_model_keys_are_unique_and_one_benchmark():
    keys = [m.key for m in MODELS]
    assert len(keys) == len(set(keys)) == 7
    assert sum(m.benchmark for m in MODELS) == 1


def test_buy_and_hold_buys_everything_once():
    bars, inst = _many()
    pf = Portfolio(cash=100_000)
    broker, _ = run(pf, bars, inst=inst, strategy=BuyAndHold(), start=date(2025, 1, 1), use_halt=False)
    assert set(pf.positions) == set(bars)
    assert len(broker.trades) == len(bars) and pf.cash >= 0
    values = [p.qty * p.avg_price for p in pf.positions.values()]
    assert max(values) / min(values) < 1.05          # equal weight


def test_momentum_holds_the_strongest_and_rotates_monthly():
    bars, inst = _many()
    pf = Portfolio(cash=100_000)
    broker, _ = run(pf, bars, inst=inst, strategy=MomentumRotation(lookback=60, top_n=2),
                    start=date(2025, 1, 1), use_halt=False)
    assert set(pf.positions) == {"S4", "S5"}         # highest drift
    assert len({t["date"] for t in broker.trades}) == 1   # ranks never change -> no churn


def test_day_trader_is_flat_every_night_and_pays_for_it():
    bars, inst = _many(days=120)
    pf = Portfolio(cash=100_000)
    costs = Costs()                                  # default commission + slippage
    broker = SimBroker(pf, costs)
    rows = run_engine(pf, bars, inst, FX, RiskLimits(), broker, OpenToClose(top_n=3),
                      start=date(2025, 1, 1), use_halt=False)
    assert pf.positions == {}
    assert all(r["n_positions"] == 0 for r in rows)
    buys = [t for t in broker.trades if t["side"] == "BUY"]
    sells = [t for t in broker.trades if t["side"] == "SELL"]
    assert len(buys) == len(sells) > 50
    assert all(b["date"] == s["date"] for b, s in zip(sorted(buys, key=lambda t: (t["date"], t["symbol"])),
                                                     sorted(sells, key=lambda t: (t["date"], t["symbol"]))))
    assert sum(t["fee"] for t in broker.trades) > 1_000


def test_mean_reversion_buys_dip_in_uptrend_and_exits_on_bounce():
    closes = [100 * 1.003 ** k for k in range(130)]
    closes += [closes[-1] * 0.97, closes[-1] * 0.95]          # two sharp down days
    closes += [closes[-1] * 1.02, closes[-1] * 1.05, closes[-1] * 1.07, closes[-1] * 1.08]
    bars = make_bars(closes)
    pf = Portfolio(cash=100_000)
    broker, _ = run(pf, {"AAA": bars}, strategy=MeanReversion(), start=bars.index[0])
    sides = [t["side"] for t in broker.trades]
    assert sides[:2] == ["BUY", "SELL"]
    assert broker.trades[0]["date"] > bars.index[130].isoformat()   # only after the dip
    assert "bounce" in broker.trades[1]["reason"] and broker.trades[1]["pnl"] > 0


def test_score_models_stay_in_cash_without_scores(uptrend):
    sentiment = next(m for m in MODELS if m.key == "sentiment")
    pf = Portfolio(cash=100_000)
    broker, _ = run(pf, {"AAA": uptrend}, strategy=sentiment.strategy, start=uptrend.index[0])
    assert broker.trades == [] and pf.cash == 100_000
    book = ScoreBook(pd.DataFrame({"symbol": ["AAA"], "date": [uptrend.index[60].isoformat()], "score": [0.7]}))
    broker, _ = run(Portfolio(cash=100_000), {"AAA": uptrend}, strategy=sentiment.strategy,
                    scores=book, start=uptrend.index[0])
    assert broker.trades and broker.trades[0]["date"] == uptrend.index[61].isoformat()


def test_liquidity_filter_and_tradable_set(uptrend):
    from app.data.providers import liquid_symbols
    thin = uptrend.copy(); thin["volume"] = 10                 # ~1 500 NOK/day
    penny = uptrend.copy() * 0.005; penny["volume"] = 10_000_000
    busy = uptrend.copy(); busy["volume"] = 50_000             # ~8 MNOK/day
    bars = {"THIN": thin, "PENNY": penny, "BUSY": busy, "NEW": busy.tail(20)}
    ok = liquid_symbols(bars, {})
    assert ok == {"BUSY"}
    inst = {s: Instrument(s) for s in bars}
    pf = Portfolio(cash=100_000)
    run(pf, bars, inst=inst, start=uptrend.index[0], tradable=ok)
    assert set(pf.positions) == {"BUSY"}
    # a position in a symbol that later turns illiquid is still managed and can be sold
    pf2 = Portfolio(cash=0, last_date=uptrend.index[-2].isoformat(),
                    positions={"THIN": Position("THIN", 10, 100, "NOK", 1e9, "2025-01-01")})
    broker, _ = run(pf2, bars, inst=inst, tradable=ok)
    assert broker.trades[0]["symbol"] == "THIN" and "THIN" not in pf2.positions
