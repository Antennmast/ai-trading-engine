from datetime import date
from types import SimpleNamespace

import pandas as pd
import pytest

from app.llm_agent import parse_scores, pick_candidates, run_agent
from app.models import MODELS
from app.risk import RiskLimits
from app.settings import LlmSettings, load_settings, stricter_limits
from app.strategy import ScoreBook
from tests.conftest import make_bars


def test_repo_config_loads_and_matches_models():
    cfg = load_settings("config.yaml", {m.key for m in MODELS})
    assert cfg.risk == RiskLimits() and cfg.starting_cash == 100_000
    assert cfg.limits_for("sprakmodell").max_positions == 4
    assert cfg.limits_for("trend") == cfg.risk and cfg.limits_for("momentum") == cfg.risk


def test_model_rules_can_only_be_stricter(tmp_path):
    shared = RiskLimits()
    assert stricter_limits(shared, {"risk_per_trade": 0.002}, "x").risk_per_trade == 0.002
    for bad in ({"risk_per_trade": 0.01}, {"max_positions": 6}, {"daily_loss_limit": 0.05},
                {"max_position_weight": 0.5}, {"max_open_risk": 0.03}):
        with pytest.raises(ValueError, match="looser"):
            stricter_limits(shared, bad, "x")
    with pytest.raises(ValueError, match="not a risk rule"):
        stricter_limits(shared, {"leverage": 2}, "x")
    with pytest.raises(ValueError, match="positive"):
        stricter_limits(shared, {"max_positions": 0}, "x")
    cfg = tmp_path / "c.yaml"
    cfg.write_text("models:\n  ukjent: {}\n")
    with pytest.raises(ValueError, match="unknown model"):
        load_settings(cfg, {"trend"})
    cfg.write_text("risk:\n  risk_per_trade: 0.5\n")
    with pytest.raises(ValueError, match="allowed ranges"):
        load_settings(cfg)


def test_parse_scores_is_defensive():
    text = ('Her er svaret:\n```json\n[{"symbol":"AAA","score":3,"reason":"sterk  trend"},'
            '{"symbol":"HACK","score":1},{"symbol":"BBB","score":"x"},'
            '{"symbol":"CCC","score":-0.4,"reason":"svak"},{"symbol":"AAA","score":-1}, 7]\n```')
    rows = parse_scores(text, {"AAA", "BBB", "CCC"})
    assert rows == [{"symbol": "AAA", "score": 1.0, "reason": "sterk trend"},
                    {"symbol": "CCC", "score": -0.4, "reason": "svak"}]
    with pytest.raises(ValueError):
        parse_scores("beklager, ingen liste", {"AAA"})


class FakeClient:
    def __init__(self, text=None, fail=False):
        self.calls, self.text, self.fail = [], text, fail
        self.messages = self

    def create(self, **kw):
        self.calls.append(kw)
        if self.fail:
            raise RuntimeError("api down")
        import json
        shares = json.loads(kw["messages"][0]["content"].split("Shares:\n")[1])
        text = self.text or json.dumps([{"symbol": s["symbol"], "score": 0.5, "reason": "ok"} for s in shares])
        return SimpleNamespace(content=[SimpleNamespace(text=text)],
                               usage=SimpleNamespace(input_tokens=1000, output_tokens=200))


def _setup():
    up = make_bars([100 * 1.004 ** i for i in range(120)])
    flat = make_bars([100.0] * 120)
    return {"UP1": up, "UP2": up * 2, "FLAT": flat, "HELD": flat}, up.index[-1]


def test_candidates_are_held_shares_plus_trend_buys():
    bars, asof = _setup()
    assert pick_candidates(bars, {"UP1", "UP2", "FLAT"}, {"HELD"}, asof, 10) == ["HELD", "UP1", "UP2"] \
        or pick_candidates(bars, {"UP1", "UP2", "FLAT"}, {"HELD"}, asof, 10) == ["HELD", "UP2", "UP1"]
    assert pick_candidates(bars, {"UP1", "UP2"}, set(), asof, 1) in (["UP1"], ["UP2"])
    assert pick_candidates(bars, {"UP1"}, set(), date(2030, 1, 1), 5) == []   # stale bars


def test_agent_writes_scores_journal_and_cost_once(tmp_path):
    bars, asof = _setup()
    client = FakeClient()
    kw = dict(bars=bars, tradable={"UP1", "UP2", "FLAT"}, held={"HELD"}, names={"UP1": "Opp AS"},
              tickers={}, asof=asof, cfg=LlmSettings(), scores_csv=tmp_path / "sig" / "llm.csv",
              state_dir=tmp_path / "state", client=client)
    out = run_agent(**kw)
    assert out["status"] == "scored" and out["shares"] == 3
    assert out["cost_usd"] == pytest.approx(3 * (1000 / 1e6 * 0.10 + 200 / 1e6 * 0.50), abs=1e-5)
    sent = client.calls[0]
    assert sent["model"] == "claude-haiku-5-5" and "untrusted" in sent["system"]
    assert ScoreBook.from_csv(tmp_path / "sig" / "llm.csv").get("UP1", asof) == 0.5
    assert "Opp AS" in (tmp_path / "state" / "journal.md").read_text()
    assert len(pd.read_csv(tmp_path / "state" / "llm_usage.csv")) == 1
    assert out["samples"] == 3 and len(client.calls) == 3
    assert run_agent(**kw)["status"] == "already scored" and len(client.calls) == 3


def test_agent_failures_never_stop_the_run(tmp_path, monkeypatch):
    bars, asof = _setup()
    kw = dict(bars=bars, tradable={"UP1"}, held=set(), names={}, tickers={}, asof=asof,
              cfg=LlmSettings(), scores_csv=tmp_path / "llm.csv", state_dir=tmp_path / "s")
    assert run_agent(**kw, client=FakeClient(fail=True))["status"].startswith("error")
    assert run_agent(**kw, client=FakeClient(text="tull"))["status"].startswith("error")
    assert not (tmp_path / "llm.csv").exists()
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert run_agent(**kw)["status"] == "no api key"
    assert run_agent(**{**kw, "cfg": LlmSettings(enabled=False)})["status"] == "disabled"


class VaryingClient(FakeClient):
    """Returns a different score for UP1 on every call; fails on the second call."""
    def create(self, **kw):
        import json
        n = len(self.calls)
        self.calls.append(kw)
        if n == 1:
            raise RuntimeError("timeout")
        score = [0.9, None, -0.1, 0.4, 0.3][n]
        rows = [{"symbol": "UP1", "score": score, "reason": f"svar {n}"}]
        if n == 0:
            rows.append({"symbol": "UP2", "score": 1.0, "reason": "bare ett svar"})
        return SimpleNamespace(content=[SimpleNamespace(text=json.dumps(rows))],
                               usage=SimpleNamespace(input_tokens=10, output_tokens=10))


def test_agent_uses_the_median_of_several_answers(tmp_path):
    bars, asof = _setup()
    out = run_agent(bars=bars, tradable={"UP1", "UP2"}, held=set(), names={}, tickers={}, asof=asof,
                    cfg=LlmSettings(samples=5), scores_csv=tmp_path / "llm.csv",
                    state_dir=tmp_path / "s", client=VaryingClient())
    assert out["status"] == "scored" and out["samples"] == 4          # one call failed
    book = ScoreBook.from_csv(tmp_path / "llm.csv")
    assert book.get("UP1", asof) == 0.3                               # median of 0.9, -0.1, 0.4, 0.3
    assert book.get("UP2", asof) is None                              # scored in only 1 of 4 answers
    assert "sprik 1.00 mellom 4 svar" in (tmp_path / "s" / "journal.md").read_text()


def test_t_stat_needs_60_days_and_detects_a_real_edge():
    from app.report import excess_t_stat
    import numpy as np
    rng = np.random.default_rng(1)
    days = [f"d{i:03d}" for i in range(121)]
    b = 100_000 * np.cumprod(1 + rng.normal(0.0003, 0.01, 121))
    good = 100_000 * np.cumprod(1 + np.diff(np.log(b), prepend=np.log(b[0])) + 0.004)
    bench = {"benchmark": True, "curve": [[d, v] for d, v in zip(days, b)]}
    model = {"benchmark": False, "curve": [[d, v] for d, v in zip(days, good)]}
    assert excess_t_stat({**model, "curve": model["curve"][:30]}, bench) == (29, None)
    n, t = excess_t_stat(model, bench)
    assert n == 120 and t > 5
    same = {"benchmark": False, "curve": bench["curve"]}
    assert excess_t_stat(same, bench) == (120, None)                  # zero variance: undefined
    assert excess_t_stat(bench, bench)[1] is None
