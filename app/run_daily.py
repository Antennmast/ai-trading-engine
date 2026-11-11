"""Scheduled play-money run: fetch bars once, advance every model's portfolio, save state.

    python -m app.run_daily                          # Yahoo data, all models
    python -m app.run_daily --models trend,momentum
    python -m app.run_daily --provider csv --data-dir data
    python -m app.run_daily --backtest 2025-01-01    # dry run from a date, nothing saved
"""
from __future__ import annotations

import argparse
import os
import traceback
from datetime import date as Date
from pathlib import Path

import pandas as pd

from app.data.providers import (CsvProvider, YahooProvider, last_completed_bar_date,
                                liquid_symbols)
from app.engine import Instrument, run_engine
from app.models import MODELS
from app.portfolio import Portfolio
from app.report import append_csv, model_summary, stats, write_outputs
from app.risk import RiskLimits
from app.sim_broker import Costs, SimBroker
from app.strategy import ScoreBook


def env(name: str, default: str) -> str:
    return os.getenv(name, default)


def load_universe(path: Path) -> tuple[dict[str, Instrument], dict[str, str]]:
    df = pd.read_csv(path, dtype={"symbol": str})
    df = df[df["enabled"].astype(str).str.lower() == "true"]
    instruments = {r.symbol: Instrument(r.symbol, r.currency) for r in df.itertuples()}
    tickers = {r.symbol: r.yahoo_ticker for r in df.itertuples()}
    return instruments, tickers


def main() -> None:
    p = argparse.ArgumentParser(description="Play-money daily run (simulated broker)")
    p.add_argument("--provider", choices=["yahoo", "csv"], default="yahoo")
    p.add_argument("--data-dir", default="data")
    p.add_argument("--universe", default="config/universe.csv")
    p.add_argument("--state-dir", default="state")
    p.add_argument("--docs-dir", default="docs")
    p.add_argument("--models", default="", help="comma-separated model keys (default: all)")
    p.add_argument("--backtest", metavar="YYYY-MM-DD", help="simulate from date; state untouched")
    args = p.parse_args()

    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    starting_cash = float(env("STARTING_CAPITAL_NOK", "100000"))
    base = env("BASE_CURRENCY", "NOK")
    limits = RiskLimits(
        risk_per_trade=float(env("RISK_PER_TRADE", "0.005")),
        max_open_risk=float(env("MAX_OPEN_RISK", "0.02")),
        max_positions=int(env("MAX_POSITIONS", "5")),
        max_position_weight=float(env("MAX_POSITION_WEIGHT", "0.20")),
        daily_loss_limit=float(env("DAILY_LOSS_LIMIT", "0.01")),
    )
    costs = Costs(
        commission_rate=float(env("SIM_COMMISSION_RATE", "0.0005")),
        min_commission=float(env("SIM_MIN_COMMISSION", "10")),
        slippage_bps=float(env("SIM_SLIPPAGE_BPS", "5")),
    )
    wanted = {k.strip() for k in args.models.split(",") if k.strip()}
    models = [m for m in MODELS if not wanted or m.key in wanted]
    if not models:
        raise SystemExit(f"No such model. Available: {', '.join(m.key for m in MODELS)}")

    instruments, tickers = load_universe(Path(args.universe))
    if not instruments:
        raise SystemExit("No enabled instruments in universe file.")

    if args.provider == "csv":
        provider, cutoff = CsvProvider(args.data_dir), None
    else:
        provider = YahooProvider(period="5y" if args.backtest else "2y")
        cutoff = last_completed_bar_date()
    bars = provider.history(tickers, cutoff)
    print(f"[data] {len(bars)}/{len(tickers)} instruments, "
          f"latest bar {max(df.index[-1] for df in bars.values()) if bars else 'n/a'}")
    if len(bars) < max(1, len(tickers) // 2):
        raise SystemExit("Price data missing for most instruments; state left untouched.")
    fx = provider.fx({i.currency for i in instruments.values()}, base)
    tradable = liquid_symbols(
        bars, {s: fx[i.currency] for s, i in instruments.items()},
        min_turnover=float(env("MIN_TURNOVER_NOK", "1000000")),
        min_price=float(env("MIN_PRICE", "1")))
    print(f"[universe] {len(tradable)} of {len(instruments)} instruments pass the liquidity filter")
    if not tradable:
        raise SystemExit("No instrument passes the liquidity filter; state left untouched.")
    free = Costs(commission_rate=0, min_commission=0, slippage_bps=0)

    def scores_for(model) -> ScoreBook:
        return ScoreBook.from_csv(model.scores_file) if model.scores_file else ScoreBook()

    if args.backtest:
        print(f"BACKTEST from {args.backtest} (dividends ignored; liquidity filter uses today's turnover)")
        print(f"{'model':13s}{'return %':>10s}{'max dd %':>10s}{'fills':>7s}{'win %':>7s}{'fees':>9s}")
        for m in models:
            pf = Portfolio(cash=starting_cash, base_currency=base)
            broker = SimBroker(pf, free if m.cost_free else costs)
            rows = run_engine(pf, bars, instruments, fx, limits, broker, m.strategy, scores_for(m),
                              start=Date.fromisoformat(args.backtest), use_halt=m.use_halt,
                              tradable=tradable)
            if not rows:
                raise SystemExit("No bars in backtest window.")
            st = stats(pd.DataFrame(rows)["equity"], pd.DataFrame(broker.trades))
            win = "–" if st["win_rate_pct"] is None else f"{st['win_rate_pct']:.0f}"
            print(f"{m.key:13s}{st['return_pct']:10.2f}{st['max_drawdown_pct']:10.2f}"
                  f"{st['fills']:7d}{win:>7s}{st['fees']:9.0f}")
        return

    state_dir = Path(args.state_dir)
    summaries, errors = [], {}
    for m in MODELS:                                # summaries always cover every model
        d = state_dir / m.key
        pf = Portfolio.load(d / "portfolio.json", starting_cash, base)
        if m in models:
            try:
                broker = SimBroker(pf, free if m.cost_free else costs)
                rows = run_engine(pf, bars, instruments, fx, limits, broker, m.strategy,
                                  scores_for(m), use_halt=m.use_halt, tradable=tradable)
                if rows:
                    # portfolio.json is written last: if a step fails the next run redoes the day
                    append_csv(d / "trades.csv", broker.trades)
                    append_csv(d / "equity.csv", rows)
                    pf.save(d / "portfolio.json")
                print(f"[{m.key}] {len(rows)} new bar(s), {len(broker.trades)} fill(s)")
            except Exception as exc:                # one broken model must not stop the others
                traceback.print_exc()
                errors[m.key] = f"{type(exc).__name__}: {exc}"
                pf = Portfolio.load(d / "portfolio.json", starting_cash, base)
        summaries.append(model_summary(m, state_dir, pf, fx, starting_cash))

    print(write_outputs(summaries, state_dir, Path(args.docs_dir), starting_cash, base, errors,
                        {"total": len(instruments), "tradable": len(tradable)})[:1500])
    if errors:
        raise SystemExit(f"Models failed: {', '.join(errors)}")


if __name__ == "__main__":
    main()
