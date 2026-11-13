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
    ask_only: bool = False       # only shares allowed on a Norwegian aksjesparekonto (ASK)
    variant_of: Optional[str] = None
    # Annual excess return over the equal-weight benchmark found in aksjescore's backtest
    # (2018-2026, top 30, 30 bp costs). Used to test whether the live result still fits.
    historical_excess: Optional[float] = None


MODELS = [
    Model("referanse", "Referanse", "Kjøp og hold alle omsettelige aksjer likt vektet, uten kostnader.",
          BuyAndHold(), use_halt=False, benchmark=True, cost_free=True),
    Model("trend", "Trend", "Kjøper aksjer i opptrend, glidende stop-loss.",
          TrendStrategy()),
    Model("momentum", "Momentum", "Holder de 10 sterkeste siste år (uten siste måned), bytter månedlig.",
          MomentumRotation(), use_halt=False, historical_excess=0.085),
    Model("momentumbrems", "Momentum med brems",
          "Som Momentum, men en aksje holdes ikke når hjemmemarkedets indeks er under 200-dagerssnittet. Den delen står i kontanter.",
          MomentumWithBrake(), use_halt=False),
    Model("tilbakefall", "Tilbakefall", "Kjøper korte dipper i aksjer i opptrend.",
          MeanReversion()),
    Model("sentiment", "Sentiment", "Trend, men kjøper bare når aksjescore er 60 eller høyere.",
          TrendStrategy(require_score=True), scores_file="signals/scores.csv"),
    Model("sprakmodell", "Språkmodell", "Trend, men en språkmodell (Claude Haiku) avgjør hvilke aksjer som kjøpes. Skriver dagbok.",
          TrendStrategy(require_score=True), scores_file="signals/llm_scores.csv"),
    Model("scoretopp", "Score topp 10", "Holder de 10 aksjene med høyest aksjescore, bytter månedlig.",
          ScoreRotation(), scores_file="signals/scores.csv", use_halt=False, historical_excess=0.083),
    Model("scoretopp_ask", "Score topp 10 ASK",
          "Som Score topp 10, men bare aksjer som kan eies på aksjesparekonto, og med kurtasje som hos norsk megler.",
          ScoreRotation(), scores_file="signals/scores.csv", use_halt=False, ask_only=True,
          variant_of="scoretopp", historical_excess=0.083),
    Model("momentum_ask", "Momentum ASK",
          "Som Momentum, men bare aksjer som kan eies på aksjesparekonto, og med kurtasje som hos norsk megler.",
          MomentumRotation(), use_halt=False, ask_only=True, variant_of="momentum", historical_excess=0.085),
    Model("dagshandel", "Dagshandel", "Kjøper på åpning, selger alt på sluttkurs samme dag.",
          OpenToClose(), use_halt=False),
]
