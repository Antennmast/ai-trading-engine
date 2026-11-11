"""The competing models. Each gets its own play-money portfolio in state/<key>/."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from app.strategy import (BuyAndHold, MeanReversion, MomentumRotation, OpenToClose,
                          TrendStrategy)


@dataclass(frozen=True)
class Model:
    key: str
    name: str                    # shown on the dashboard (Norwegian)
    blurb: str
    strategy: object
    scores_file: Optional[str] = None
    use_halt: bool = True        # daily loss halt (only meaningful for risk-sized models)
    benchmark: bool = False
    cost_free: bool = False      # the benchmark stands in for "the market": no fees or slippage


MODELS = [
    Model("referanse", "Referanse", "Kjøp og hold alle omsettelige aksjer likt vektet, uten kostnader.",
          BuyAndHold(), use_halt=False, benchmark=True, cost_free=True),
    Model("trend", "Trend", "Kjøper aksjer i opptrend, glidende stop-loss.",
          TrendStrategy()),
    Model("momentum", "Momentum", "Holder de 4 sterkeste siste halvår, bytter månedlig.",
          MomentumRotation(), use_halt=False),
    Model("tilbakefall", "Tilbakefall", "Kjøper korte dipper i aksjer i opptrend.",
          MeanReversion()),
    Model("sentiment", "Sentiment", "Trend, men kjøper bare når aksjescore er 60 eller høyere.",
          TrendStrategy(require_score=True), scores_file="signals/scores.csv"),
    Model("sprakmodell", "Språkmodell", "Trend + score fra språkmodell. Venter på signaler i signals/llm_scores.csv.",
          TrendStrategy(require_score=True), scores_file="signals/llm_scores.csv"),
    Model("dagshandel", "Dagshandel", "Kjøper på åpning, selger alt på sluttkurs samme dag.",
          OpenToClose(), use_halt=False),
]
