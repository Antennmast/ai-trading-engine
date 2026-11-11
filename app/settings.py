"""Loads config.yaml and enforces that per-model rules are never looser than the shared ones."""
from __future__ import annotations

from dataclasses import dataclass, field, fields, replace
from pathlib import Path

import yaml

from app.risk import RiskLimits
from app.sim_broker import Costs


@dataclass(frozen=True)
class LlmSettings:
    enabled: bool = True
    model: str = "claude-haiku-5-5"
    max_candidates: int = 15
    samples: int = 3
    max_tokens: int = 2000
    usd_per_mtok_input: float = 0.10
    usd_per_mtok_output: float = 0.50


@dataclass(frozen=True)
class Settings:
    starting_cash: float = 100_000.0
    base_currency: str = "NOK"
    risk: RiskLimits = RiskLimits()
    costs: Costs = Costs()
    min_turnover: float = 1_000_000.0
    min_price: float = 1.0
    model_risk: dict = field(default_factory=dict)      # key -> RiskLimits
    llm: LlmSettings = LlmSettings()
    index_name: str = ""
    index_ticker: str = ""
    market_indices: dict = field(default_factory=dict)   # currency -> (name, yahoo ticker)
    ask_costs: Costs = Costs(commission_rate=0.0015, min_commission=29.0)
    ask_foreign_dividend_withholding: float = 0.15

    def limits_for(self, key: str) -> RiskLimits:
        return self.model_risk.get(key, self.risk)


def stricter_limits(shared: RiskLimits, overrides: dict, model_key: str) -> RiskLimits:
    """Apply overrides; every risk field is 'lower = stricter', so higher is refused."""
    known = {f.name for f in fields(RiskLimits)}
    for name, value in overrides.items():
        if name not in known:
            raise ValueError(f"config.yaml: models.{model_key}.{name} is not a risk rule "
                             f"(allowed: {', '.join(sorted(known))})")
        if not value > 0:
            raise ValueError(f"config.yaml: models.{model_key}.{name} must be positive")
        if value > getattr(shared, name):
            raise ValueError(
                f"config.yaml: models.{model_key}.{name}={value} is looser than the shared "
                f"rule ({getattr(shared, name)}). Per-model rules may only be stricter.")
    return replace(shared, **overrides)


def load_settings(path="config.yaml", known_models: set[str] | None = None) -> Settings:
    raw = yaml.safe_load(Path(path).read_text()) if Path(path).exists() else {}
    raw = raw or {}
    risk = RiskLimits(**(raw.get("risk") or {}))
    if not (0 < risk.risk_per_trade <= 0.02 and 0 < risk.max_open_risk <= 0.10
            and 0 < risk.max_position_weight <= 1 and risk.max_positions >= 1
            and 0 < risk.daily_loss_limit <= 0.10):
        raise ValueError("config.yaml: shared risk rules are outside the allowed ranges")
    model_risk = {}
    for key, overrides in (raw.get("models") or {}).items():
        if known_models is not None and key not in known_models:
            raise ValueError(f"config.yaml: unknown model '{key}'")
        model_risk[key] = stricter_limits(risk, overrides or {}, key)
    uni, llm = raw.get("universe") or {}, raw.get("llm") or {}
    index = raw.get("benchmark_index") or {}
    ask = dict(raw.get("ask") or {})
    withholding = float(ask.pop("foreign_dividend_withholding", 0.15))
    if not 0 <= withholding < 1:
        raise ValueError("config.yaml: ask.foreign_dividend_withholding must be between 0 and 1")
    prices = llm.get("usd_per_mtok") or {}
    return Settings(
        starting_cash=float(raw.get("starting_cash", 100_000)),
        base_currency=str(raw.get("base_currency", "NOK")),
        risk=risk,
        costs=Costs(**(raw.get("costs") or {})),
        min_turnover=float(uni.get("min_turnover_nok", 1_000_000)),
        min_price=float(uni.get("min_price", 1)),
        model_risk=model_risk,
        ask_costs=Costs(**{"commission_rate": 0.0015, "min_commission": 29.0, **ask}),
        ask_foreign_dividend_withholding=withholding,
        index_name=str(index.get("name", "")),
        index_ticker=str(index.get("yahoo_ticker", "")),
        market_indices={str(c): (str(v.get("name", c)), str(v.get("yahoo_ticker", "")))
                        for c, v in (raw.get("market_indices") or {}).items()},
        llm=LlmSettings(
            enabled=bool(llm.get("enabled", True)),
            model=str(llm.get("model", "claude-haiku-5-5")),
            max_candidates=int(llm.get("max_candidates", 15)),
            samples=max(1, min(int(llm.get("samples", 3)), 5)),
            max_tokens=int(llm.get("max_tokens", 2000)),
            usd_per_mtok_input=float(prices.get("input", 0.10)),
            usd_per_mtok_output=float(prices.get("output", 0.50)),
        ),
    )
