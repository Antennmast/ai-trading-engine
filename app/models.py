"""The competing models. Each gets its own play-money portfolio in state/<key>/."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from app.strategy import (BuyAndHold, MeanReversion, MomentumRotation, MomentumWithBrake,
                          OpenToClose, ScoreRotation, TrendStrategy)


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
    Model("momentum", "Momentum", "Holder de 10 sterkeste siste år (uten siste måned), bytter månedlig.",
          MomentumRotation(), use_halt=False),
    Model("momentumbrems", "Momentum med brems",
          "Som Momentum, men selger alt når OSEBX ligger under 200-dagerssnittet.",
          MomentumWithBrake(), use_halt=False),
    Model("tilbakefall", "Tilbakefall", "Kjøper korte dipper i aksjer i opptrend.",
          MeanReversion()),
    Model("sentiment", "Sentiment", "Trend, men kjøper bare når aksjescore er 60 eller høyere.",
          TrendStrategy(require_score=True), scores_file="signals/scores.csv"),
    Model("sprakmodell", "Språkmodell", "Trend, men en språkmodell (Claude Haiku) avgjør hvilke aksjer som kjøpes. Skriver dagbok.",
          TrendStrategy(require_score=True), scores_file="signals/llm_scores.csv"),
    Model("scoretopp", "Score topp 10", "Holder de 10 aksjene med høyest aksjescore, bytter månedlig.",
          ScoreRotation(), scores_file="signals/scores.csv", use_halt=False),
    Model("dagshandel", "Dagshandel", "Kjøper på åpning, selger alt på sluttkurs samme dag.",
          OpenToClose(), use_halt=False),
]
