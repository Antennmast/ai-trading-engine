from app.risk import RiskLimits, daily_loss_breached, size_position

L = RiskLimits()  # 0.5 % per trade, 2 % open risk, 5 positions, 20 % weight, 1 % daily loss
BASE = dict(equity=100_000, cash=100_000, fx=1.0, open_risk=0.0, n_open=0, limits=L)


def test_sized_by_trade_risk():
    r = size_position(price=100, stop=90, **BASE)          # 500 NOK risk / 10 per share
    assert r.qty == 50 and "risk per trade" in r.reason


def test_capped_by_position_weight():
    r = size_position(price=100, stop=99, **BASE)          # risk says 500 sh, weight says 200
    assert r.qty == 200 and "weight" in r.reason


def test_capped_by_cash():
    r = size_position(price=100, stop=99, **{**BASE, "cash": 5_000})
    assert r.qty == 49 and "cash" in r.reason              # 5000 / (100 * 1.003)


def test_fx_conversion_reduces_quantity():
    r = size_position(price=100, stop=90, **{**BASE, "fx": 11.5})   # EUR stock, NOK book
    assert r.qty == 4                                      # 500 / (10 * 11.5) = 4.3


def test_aggregate_open_risk_limit():
    r = size_position(price=100, stop=90, **{**BASE, "open_risk": 1_800})
    assert r.qty == 20                                     # only 200 NOK of risk budget left
    assert size_position(price=100, stop=90, **{**BASE, "open_risk": 2_000}).qty == 0


def test_max_positions_and_bad_stop():
    assert size_position(price=100, stop=90, **{**BASE, "n_open": 5}).qty == 0
    assert size_position(price=100, stop=100, **BASE).qty == 0
    assert size_position(price=100, stop=0, **BASE).qty == 0


def test_daily_loss_halt():
    assert daily_loss_breached(100_000, 98_900, L)
    assert not daily_loss_breached(100_000, 99_500, L)
    assert not daily_loss_breached(None, 99_000, L)
