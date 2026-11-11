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
NO_COST = Costs(commission_rate=0, min_commission=0, slippage_bps=0, min_order_value=0)


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
    assert len(keys) == len(set(keys)) == 9
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
    broker, _ = run(pf, bars, inst=inst, strategy=MomentumRotation(lookback=60, skip=5, top_n=2),
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


def test_liquidity_filter_drops_shares_with_corporate_action_jumps(uptrend):
    from app.data.providers import liquid_symbols
    busy = uptrend.copy(); busy["volume"] = 50_000
    paid_out = busy.copy()
    paid_out.iloc[60:, :4] = paid_out.iloc[60:, :4] * 0.05     # 95 % capital distribution
    paid_out["volume"] = 5_000_000
    assert liquid_symbols({"BUSY": busy, "SHELL": paid_out}, {}) == {"BUSY"}


def test_benchmark_includes_expensive_shares_with_fractions():
    cheap = make_bars([50.0] * 5); pricey = make_bars([40_000.0] * 5)
    bars = {"CHEAP": cheap, "PRICEY": pricey}
    inst = {s: Instrument(s) for s in bars}
    pf = Portfolio(cash=100_000)
    run(pf, bars, inst=inst, strategy=BuyAndHold(), start=cheap.index[0], use_halt=False)
    assert set(pf.positions) == {"CHEAP", "PRICEY"}
    assert 1.0 < pf.positions["PRICEY"].qty < 1.5 and pf.cash >= 0
    values = [p.qty * p.avg_price for p in pf.positions.values()]
    assert max(values) / min(values) < 1.01
    # whole-share models are unchanged: momentum cannot buy a share it cannot afford one of
    pf2 = Portfolio(cash=30_000)
    run(pf2, bars, inst=inst, strategy=MomentumRotation(lookback=2, skip=0, top_n=2), start=cheap.index[0], use_halt=False)
    assert "PRICEY" not in pf2.positions and all(float(p.qty).is_integer() for p in pf2.positions.values())


# ---- dividends, splits, score rotation --------------------------------------
def _with_actions(closes, div=None, split=None, **kw):
    bars = make_bars(closes, **kw)
    bars["dividends"] = 0.0; bars["splits"] = 0.0
    for i, v in (div or {}).items():
        bars.iloc[i, bars.columns.get_loc("dividends")] = v
    for i, v in (split or {}).items():
        bars.iloc[i, bars.columns.get_loc("splits")] = v
    return bars


def test_dividend_is_credited_and_does_not_trigger_the_stop():
    # 100 -> ex-dividend 5 -> opens and trades at 95; the stop was 96
    bars = _with_actions([100, 95, 95], div={1: 5.0}, opens=[None, 95, None], lows=[None, 94.5, None])
    pf = Portfolio(cash=0, last_date=bars.index[0].isoformat(), last_equity=1000,
                   positions={"AAA": Position("AAA", 10, 100, "NOK", 96, "2024-12-31")})
    broker, rows = run(pf, {"AAA": bars}, strategy=MomentumRotation(lookback=500), use_halt=False)
    assert "AAA" in pf.positions                       # not stopped out by the ex-dividend drop
    assert pf.cash == 50 and pf.positions["AAA"].stop == 91
    assert rows[0]["equity"] == 1000                   # 950 in shares + 50 in cash: no fake loss
    assert broker.trades[0]["side"] == "DIV" and broker.trades[0]["pnl"] == 50


def test_dividend_not_paid_to_shares_bought_on_the_ex_date_and_bad_data_ignored():
    bars = _with_actions([100, 95, 95], div={1: 5.0})
    pf = Portfolio(cash=10_000, last_date=bars.index[0].isoformat(),
                   pending=[PendingOrder("AAA", "BUY", 10, bars.index[0].isoformat(), 0)])
    broker, _ = run(pf, {"AAA": bars}, strategy=BuyAndHold(), use_halt=False)
    assert [t["side"] for t in broker.trades if t["symbol"] == "AAA"][0] == "BUY"
    assert not [t for t in broker.trades if t["side"] == "DIV"]
    crazy = _with_actions([100, 100], div={1: 80.0})
    pf = Portfolio(cash=0, last_date=crazy.index[0].isoformat(),
                   positions={"AAA": Position("AAA", 10, 100, "NOK", 0, "2024-12-31")})
    run(pf, {"AAA": crazy}, strategy=BuyAndHold(), use_halt=False)
    assert pf.cash == 0


def test_split_keeps_value_and_rescales_stop_and_pending():
    bars = _with_actions([10, 10, 10], split={1: 10.0})        # history is already split-adjusted
    pf = Portfolio(cash=0, last_date=bars.index[0].isoformat(), last_prices={"AAA": 100.0},
                   positions={"AAA": Position("AAA", 5, 100, "NOK", 90, "2024-12-31")},
                   pending=[PendingOrder("BBB", "BUY", 3, bars.index[0].isoformat(), 80)])
    broker, rows = run(pf, {"AAA": bars}, strategy=BuyAndHold(), use_halt=False,
                       inst={"AAA": Instrument("AAA"), "BBB": Instrument("BBB")})
    pos = pf.positions["AAA"]
    assert (pos.qty, pos.avg_price, pos.stop) == (50, 10, 9) and rows[0]["equity"] == 500
    assert not [t for t in broker.trades if t["reason"] == "stop hit"]
    b = SimBroker(Portfolio(cash=0, pending=[PendingOrder("X", "BUY", 3, "2025-01-01", 80)]), NO_COST)
    b.split("X", 4)
    assert (b.pf.pending[0].qty, b.pf.pending[0].stop) == (12, 20)


def test_total_return_view_removes_the_ex_dividend_drop():
    from app.data.providers import total_return_view
    bars = _with_actions([100, 100, 95, 95], div={2: 5.0})
    view = total_return_view(bars)
    assert list(view["close"].round(4)) == [95, 95, 95, 95]     # flat total return
    assert view["close"].iloc[-1] == bars["close"].iloc[-1]     # anchored at the traded price
    assert total_return_view(make_bars([1, 2, 3])) is not None  # no dividend columns: unchanged


def test_score_rotation_holds_top_scores_and_waits_without_scores():
    from app.strategy import ScoreRotation
    bars, inst = _many(n=6, days=30)
    day = bars["S0"].index[10].isoformat()
    book = ScoreBook(pd.DataFrame({"symbol": [f"S{i}" for i in range(6)], "date": [day] * 6,
                                   "score": [0.1, 0.9, -0.2, 0.8, 0.3, 0.7]}))
    pf = Portfolio(cash=100_000)
    run(pf, bars, inst=inst, strategy=ScoreRotation(top_n=3), scores=book,
        start=bars["S0"].index[10], use_halt=False)
    assert set(pf.positions) == {"S1", "S3", "S5"} and pf.cash >= 0
    pf = Portfolio(cash=100_000)
    broker, _ = run(pf, bars, inst=inst, strategy=ScoreRotation(top_n=3), start=bars["S0"].index[0], use_halt=False)
    assert broker.trades == [] and pf.pending == []


def test_benchmark_reinvests_dividends_in_the_same_share():
    bars = _with_actions([100, 95, 95], div={1: 5.0}, opens=[None, 95, None])
    pf = Portfolio(cash=0, last_date=bars.index[0].isoformat(), last_equity=1000,
                   positions={"AAA": Position("AAA", 10.0, 100, "NOK", 0, "2024-12-31")})
    broker, rows = run(pf, {"AAA": bars}, strategy=BuyAndHold(), use_halt=False)
    assert [t["side"] for t in broker.trades] == ["DIV", "BUY"]
    assert 10.5 < pf.positions["AAA"].qty < 10.53 and 0 <= pf.cash < 1
    assert rows[-1]["equity"] == pytest.approx(1000, abs=1)       # total return is flat
    # ordinary models keep the dividend as cash
    pf = Portfolio(cash=0, last_date=bars.index[0].isoformat(),
                   positions={"AAA": Position("AAA", 10, 100, "NOK", 0, "2024-12-31")})
    broker, _ = run(pf, {"AAA": bars}, strategy=MomentumRotation(), use_halt=False)
    assert pf.cash == 50 and pf.positions["AAA"].qty == 10


def test_position_is_closed_when_its_share_stops_trading():
    live = make_bars([100.0] * 20)
    dead = live.iloc[:3] * 0.5                                    # last bar on day 3 at 50
    inst = {"LIVE": Instrument("LIVE"), "DEAD": Instrument("DEAD")}
    pf = Portfolio(cash=0, last_date=live.index[0].isoformat(),
                   positions={"DEAD": Position("DEAD", 10, 60, "NOK", 40, live.index[0].isoformat())})
    broker, rows = run(pf, {"LIVE": live, "DEAD": dead}, inst=inst, strategy=MomentumRotation(lookback=500),
                       use_halt=False)
    sells = [t for t in broker.trades if t["symbol"] == "DEAD"]
    assert len(sells) == 1 and sells[0]["price"] == 50 and "delisted" in sells[0]["reason"]
    gap = (pd.Timestamp(sells[0]["date"]) - pd.Timestamp(dead.index[-1])).days
    assert 10 < gap <= 14 and "DEAD" not in pf.positions and pf.cash == 500
    assert "DEAD" not in pf.last_price_dates
    # a short gap (holiday on one exchange) does not close anything
    pf = Portfolio(cash=0, last_date=live.index[0].isoformat(),
                   positions={"DEAD": Position("DEAD", 10, 60, "NOK", 40, live.index[0].isoformat())})
    broker, _ = run(pf, {"LIVE": live.iloc[:8], "DEAD": dead}, inst=inst,
                    strategy=MomentumRotation(lookback=500), use_halt=False)
    assert "DEAD" in pf.positions and broker.trades == []


def test_momentum_skips_the_most_recent_month():
    steady = make_bars([100 * 1.002 ** k for k in range(300)])
    # weak for a year, then a spike in the last month: 12-1 momentum must ignore the spike
    spike = make_bars([100 * 0.9995 ** k for k in range(280)] + [100 * 1.03 ** k for k in range(20)])
    bars = {"STEADY": steady, "SPIKE": spike}
    inst = {s_: Instrument(s_) for s_ in bars}
    pf = Portfolio(cash=100_000)
    run(pf, bars, inst=inst, strategy=MomentumRotation(top_n=1), start=steady.index[-3], use_halt=False)
    assert set(pf.positions) == {"STEADY"}


def _brake_setup():
    n = 330
    start = date(2025, 1, 1)
    bars = {s: make_bars([100 * (1 + d) ** k for k in range(n)], start=start)
            for s, d in {"NO1": 0.0020, "NO2": 0.0018, "SE1": 0.0022, "SE2": 0.0019, "SE3": 0.0001}.items()}
    inst = {s: Instrument(s, "NOK" if s.startswith("NO") else "SEK") for s in bars}
    return bars, inst, list(bars["NO1"].index)


def test_market_brake_keeps_the_braked_slice_in_cash():
    from app.strategy import MomentumWithBrake
    bars, inst, days = _brake_setup()
    fx = {"NOK": 1.0, "SEK": 1.0}
    up = pd.Series([100 + 0.1 * k for k in range(len(days))], index=days)
    falling = pd.Series([100 + 0.1 * k for k in range(280)] + [128 - 2.0 * k for k in range(len(days) - 280)], index=days)
    strat = MomentumWithBrake(top_n=4)
    kw = dict(use_halt=False, market={"NOK": up, "SEK": falling})
    pf = Portfolio(cash=100_000)
    broker = SimBroker(pf, NO_COST)
    run_engine(pf, {s: b.iloc[:275] for s, b in bars.items()}, inst, fx, RiskLimits(), broker, strat,
               start=days[270], **kw)
    assert set(pf.positions) == {"NO1", "NO2", "SE1", "SE2"}     # both markets above their averages
    invested_before = pf.positions_value(fx)
    run_engine(pf, bars, inst, fx, RiskLimits(), broker, strat, **kw)
    assert set(pf.positions) == {"NO1", "NO2"}                   # Swedish winners sold at the re-rank
    sells = [t for t in broker.trades if t["side"] == "SELL"]
    assert {t["symbol"] for t in sells} == {"SE1", "SE2"} and all("market brake" in t["reason"] for t in sells)
    assert len({t["date"][:7] for t in sells}) == 1              # sold once, at a month shift
    # the freed money stays in cash: nothing new was bought anywhere
    assert len([t for t in broker.trades if t["side"] == "BUY"]) == 4
    assert pf.cash > 0.4 * pf.equity(fx)


def test_market_brake_waits_for_a_clear_recovery_and_never_churns():
    from app.strategy import MomentumWithBrake
    bars, inst, days = _brake_setup()
    fx = {"NOK": 1.0, "SEK": 1.0}
    flat_then_dip = [100.0] * 250 + [99.9] * 30 + [100.5] * 25 + [103.0] * (len(days) - 305)
    market = pd.Series(flat_then_dip, index=days)                # one index for every market
    strat = MomentumWithBrake(top_n=2)
    pf = Portfolio(cash=100_000)
    broker = SimBroker(pf, NO_COST)
    kw = dict(use_halt=False, market=market)
    run_engine(pf, {s: b.iloc[:280] for s, b in bars.items()}, inst, fx, RiskLimits(), broker, strat,
               start=days[262], **kw)
    assert pf.positions == {} and broker.trades == []            # index just below its average
    run_engine(pf, {s: b.iloc[:305] for s, b in bars.items()}, inst, fx, RiskLimits(), broker, strat, **kw)
    assert pf.positions == {} and pf.pending == []               # above, but less than 1 %: still waiting
    run_engine(pf, bars, inst, fx, RiskLimits(), broker, strat, **kw)
    assert len(pf.positions) == 2 and len(broker.trades) == 2    # in once the index is clearly above
    plain = Portfolio(cash=100_000)                              # no index data: plain momentum
    run_engine(plain, {s: b.iloc[:280] for s, b in bars.items()}, inst, fx, RiskLimits(),
               SimBroker(plain, NO_COST), strat, start=days[262], use_halt=False, market=None)
    assert len(plain.positions) == 2


def test_equal_weight_index_from_own_shares(uptrend):
    from app.data.providers import equal_weight_index
    bars = {f"S{i}": uptrend * (i + 1) for i in range(6)}
    idx = equal_weight_index(bars, list(bars))
    assert len(idx) == len(uptrend) and idx.iloc[-1] / idx.iloc[0] == pytest.approx(
        uptrend["close"].iloc[-1] / uptrend["close"].iloc[0], rel=1e-6)
    assert equal_weight_index(bars, ["S0", "S1"]) is None       # too few shares to be an index


def test_tiny_leftover_orders_are_skipped():
    # six identical uptrends: the open-risk cap leaves only crumbs for the last position
    bars = {f"S{i}": make_bars([100 * 1.004 ** k for k in range(120)]) for i in range(6)}
    inst = {s: Instrument(s) for s in bars}
    pf = Portfolio(cash=100_000)
    broker = SimBroker(pf, Costs(min_order_value=2000))
    run_engine(pf, bars, inst, FX, RiskLimits(max_open_risk=0.0152), broker, start=date(2025, 1, 1))
    buys = [t for t in broker.trades if t["side"] == "BUY"]
    assert buys and all(t["qty"] * t["price"] >= 2000 for t in buys)
    loose = Portfolio(cash=100_000)
    b2 = SimBroker(loose, Costs(min_order_value=0))
    run_engine(loose, bars, inst, FX, RiskLimits(max_open_risk=0.0152), b2, start=date(2025, 1, 1))
    assert min(t["qty"] * t["price"] for t in b2.trades if t["side"] == "BUY") < 2000   # the crumb existed
