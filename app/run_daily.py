"""Scheduled play-money run: fetch bars once, advance every model's portfolio, save state.

    python -m app.run_daily                          # Yahoo data, all models
    python -m app.run_daily --models trend,momentum
    python -m app.run_daily --provider csv --data-dir data
    python -m app.run_daily --backtest 2025-01-01    # dry run from a date, nothing saved
"""
from __future__ import annotations

import argparse
import traceback
from datetime import date as Date
from pathlib import Path

import pandas as pd

from app.data.providers import (CsvProvider, YahooProvider, equal_weight_index,
                                last_completed_bar_date, liquid_symbols)
from app.engine import Instrument, run_engine
from app.llm_agent import run_agent
from app.models import MODELS
from app.portfolio import Portfolio
from app.report import append_csv, model_summary, stats, write_outputs
from app.settings import load_settings
from app.sim_broker import Costs, SimBroker
from app.strategy import ScoreBook


def load_universe(path: Path):
    df = pd.read_csv(path, dtype={"symbol": str})
    df = df[df["enabled"].astype(str).str.lower() == "true"]
    instruments = {r.symbol: Instrument(r.symbol, r.currency) for r in df.itertuples()}
    tickers = {r.symbol: r.yahoo_ticker for r in df.itertuples()}
    names = dict(zip(df["symbol"], df["name"])) if "name" in df.columns else {}
    ask = set(df.loc[df["ask"].astype(str).str.lower() == "true", "symbol"]) if "ask" in df.columns else set()
    return instruments, tickers, names, ask


def main() -> None:
    p = argparse.ArgumentParser(description="Play-money daily run (simulated broker)")
    p.add_argument("--provider", choices=["yahoo", "csv"], default="yahoo")
    p.add_argument("--data-dir", default="data")
    p.add_argument("--universe", default="config/universe.csv")
    p.add_argument("--state-dir", default="state")
    p.add_argument("--docs-dir", default="docs")
    p.add_argument("--config", default="config.yaml")
    p.add_argument("--news-db", default="_aksjescore/pipeline/aksjescore.db",
                   help="optional aksjescore database with news headlines for the LLM agent")
    p.add_argument("--models", default="", help="comma-separated model keys (default: all)")
    p.add_argument("--backtest", metavar="YYYY-MM-DD", help="simulate from date; state untouched")
    args = p.parse_args()

    cfg = load_settings(args.config, {m.key for m in MODELS})
    starting_cash, base, costs = cfg.starting_cash, cfg.base_currency, cfg.costs
    wanted = {k.strip() for k in args.models.split(",") if k.strip()}
    models = [m for m in MODELS if not wanted or m.key in wanted]
    if not models:
        raise SystemExit(f"No such model. Available: {', '.join(m.key for m in MODELS)}")

    instruments, tickers, names, ask_ok = load_universe(Path(args.universe))
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
    n_div = sum(int((df["dividends"] > 0).sum()) for df in bars.values())
    n_split = sum(int((df["splits"] > 0).sum()) for df in bars.values())
    print(f"[data] history contains {n_div} dividend dates and {n_split} splits")
    if len(bars) < max(1, len(tickers) // 2):
        raise SystemExit("Price data missing for most instruments; state left untouched.")
    newest = max(df.index[-1] for df in bars.values())
    if args.provider == "yahoo" and (Date.today() - newest).days > 5:
        raise SystemExit(f"Newest price bar is {newest}: the data source looks stale; state left untouched.")
    index_close = None
    if cfg.index_ticker and args.provider == "yahoo":
        idx = provider.history({cfg.index_name: cfg.index_ticker}, cutoff).get(cfg.index_name)
        index_close = None if idx is None else idx["close"]
        print(f"[index] {cfg.index_name}: {0 if idx is None else len(idx)} bars")
    fx = provider.fx({i.currency for i in instruments.values()}, base)
    print("[fx] " + ", ".join(f"{c}={v:.4f}" for c, v in sorted(fx.items())))
    no_fx = {s for s, i in instruments.items() if i.currency not in fx}
    held_no_fx = set()
    for m in MODELS:
        held_no_fx |= no_fx & set(Portfolio.load(Path(args.state_dir) / m.key / "portfolio.json",
                                                 starting_cash, base).positions)
    if held_no_fx:        # cannot value held positions: stop rather than write wrong equity
        raise SystemExit(f"No FX rate for held positions {sorted(held_no_fx)}; state left untouched.")
    instruments = {s: i for s, i in instruments.items() if s not in no_fx}
    bars = {s: df for s, df in bars.items() if s in instruments}
    tradable = liquid_symbols(
        bars, {s: fx[i.currency] for s, i in instruments.items()},
        min_turnover=cfg.min_turnover, min_price=cfg.min_price)
    print(f"[universe] {len(tradable)} of {len(instruments)} instruments pass the liquidity filter")
    if not tradable:
        raise SystemExit("No instrument passes the liquidity filter; state left untouched.")
    free = Costs(commission_rate=0, min_commission=0, slippage_bps=0)
    tradable_ask = tradable & ask_ok
    foreign_tax = {c: cfg.ask_foreign_dividend_withholding
                   for c in {i.currency for i in instruments.values()} if c != base}
    print(f"[universe] {len(tradable_ask)} of the tradable shares can be held on an ASK")

    def setup(m):
        """Costs, buyable set and dividend tax for one model."""
        if m.ask_only:
            return cfg.ask_costs, tradable_ask, foreign_tax
        return (free if m.cost_free else costs), tradable, None

    # One index per home market for the market brake; fall back to our own shares.
    markets = {}
    for ccy in sorted({i.currency for i in instruments.values()}):
        name, ticker = cfg.market_indices.get(ccy, (ccy, ""))
        series = None
        if ticker and args.provider == "yahoo":
            got = provider.history({name: ticker}, cutoff).get(name)
            series = None if got is None or len(got) < 200 else got["close"]
        if series is None:
            name = f"own equal-weight index ({ccy})"
            series = equal_weight_index(bars, [s for s in tradable if instruments[s].currency == ccy])
        if series is None or len(series) < 200:
            print(f"[brake] {ccy}: no index data, brake inactive for this market")
            continue
        markets[ccy] = series
        last, avg = float(series.iloc[-1]), float(series.tail(200).mean())
        print(f"[brake] {ccy} {name}: last {last:.1f}, 200-day average {avg:.1f} -> "
              f"{'ON' if last < avg else 'off'}")

    def scores_for(model) -> ScoreBook:
        return ScoreBook.from_csv(model.scores_file) if model.scores_file else ScoreBook()

    if args.backtest:
        print(f"BACKTEST from {args.backtest} (dividends ignored; liquidity filter uses today's turnover)")
        print(f"{'model':13s}{'return %':>10s}{'max dd %':>10s}{'fills':>7s}{'win %':>7s}{'fees':>9s}")
        for m in models:
            pf = Portfolio(cash=starting_cash, base_currency=base)
            m_costs, m_tradable, m_tax = setup(m)
            broker = SimBroker(pf, m_costs)
            rows = run_engine(pf, bars, instruments, fx, cfg.limits_for(m.key), broker, m.strategy, scores_for(m),
                              start=Date.fromisoformat(args.backtest), use_halt=m.use_halt,
                              tradable=m_tradable, market=markets, dividend_tax=m_tax)
            if not rows:
                raise SystemExit("No bars in backtest window.")
            st = stats(pd.DataFrame(rows)["equity"], pd.DataFrame(broker.trades))
            win = "–" if st["win_rate_pct"] is None else f"{st['win_rate_pct']:.0f}"
            print(f"{m.key:13s}{st['return_pct']:10.2f}{st['max_drawdown_pct']:10.2f}"
                  f"{st['fills']:7d}{win:>7s}{st['fees']:9.0f}")
        return

    state_dir = Path(args.state_dir)

    # The language-model agent scores candidates before the models run (once per bar date).
    llm_model = next((m for m in models if m.key == "sprakmodell"), None)
    if llm_model is not None:
        asof = max(df.index[-1] for df in bars.values())
        held = set(Portfolio.load(state_dir / "sprakmodell" / "portfolio.json", starting_cash, base).positions)
        status = run_agent(bars=bars, tradable=tradable, held=held, names=names, tickers=tickers,
                           asof=asof, cfg=cfg.llm, scores_csv=llm_model.scores_file,
                           state_dir=state_dir / "sprakmodell", news_db=args.news_db)
        print(f"[llm] {status}")

    summaries, errors = [], {}
    for m in MODELS:                                # summaries always cover every model
        d = state_dir / m.key
        pf = Portfolio.load(d / "portfolio.json", starting_cash, base)
        if m in models:
            try:
                m_costs, m_tradable, m_tax = setup(m)
                broker = SimBroker(pf, m_costs)
                rows = run_engine(pf, bars, instruments, fx, cfg.limits_for(m.key), broker, m.strategy,
                                  scores_for(m), use_halt=m.use_halt, tradable=m_tradable,
                                  market=markets, dividend_tax=m_tax)
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
                        {"total": len(instruments), "tradable": len(tradable)},
                        index=(cfg.index_name, index_close))[:1500])
    if errors:
        raise SystemExit(f"Models failed: {', '.join(errors)}")


if __name__ == "__main__":
    main()
