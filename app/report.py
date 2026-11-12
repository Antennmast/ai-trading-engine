"""Per-model CSV logs, the combined REPORT.md and docs/data.json for the dashboard."""
from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from app.portfolio import Portfolio


def append_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists()
    with path.open("a", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        if new:
            w.writeheader()
        w.writerows(rows)


def stats(equity: pd.Series, trades: pd.DataFrame | None = None) -> dict:
    out = {"start": float(equity.iloc[0]), "end": float(equity.iloc[-1])}
    out["return_pct"] = (out["end"] / out["start"] - 1) * 100
    out["max_drawdown_pct"] = float((equity / equity.cummax() - 1).min() * 100)
    out["fills"] = out["closed_trades"] = 0
    out["win_rate_pct"] = None
    out["fees"] = out["dividends"] = 0.0
    if trades is not None and len(trades):
        sells = trades[trades["side"] == "SELL"]
        out["fills"] = int((trades["side"] != "DIV").sum())
        out["dividends"] = float(trades.loc[trades["side"] == "DIV", "pnl"].sum())
        out["closed_trades"] = int(len(sells))
        out["win_rate_pct"] = float((sells["pnl"] > 0).mean() * 100) if len(sells) else None
        out["fees"] = float(trades["fee"].sum())
    return out


def model_summary(model, state_dir: Path, pf: Portfolio, fx: dict, starting_cash: float) -> dict:
    """Everything the dashboard needs about one model, read back from its state dir."""
    d = Path(state_dir) / model.key
    curve = pd.read_csv(d / "equity.csv") if (d / "equity.csv").exists() else pd.DataFrame()
    trades = pd.read_csv(d / "trades.csv", dtype={"symbol": str}) if (d / "trades.csv").exists() else pd.DataFrame()
    # Show the value booked at the last processed close, not a revaluation at today's FX.
    eq = pf.last_equity if pf.last_equity else pf.equity(fx)
    st = stats(curve["equity"], trades) if len(curve) else {
        "max_drawdown_pct": 0.0, "fills": 0, "closed_trades": 0, "win_rate_pct": None, "fees": 0.0,
        "dividends": 0.0}
    return {
        "key": model.key, "name": model.name, "blurb": model.blurb, "benchmark": model.benchmark,
        "variant_of": model.variant_of, "ask_only": model.ask_only,
        "last_date": pf.last_date,
        "equity": round(eq, 2), "cash": round(pf.cash, 2),
        "return_pct": round((eq / starting_cash - 1) * 100, 3),
        "max_drawdown_pct": round(st["max_drawdown_pct"], 3),
        "fills": st["fills"], "closed_trades": st["closed_trades"],
        "win_rate_pct": None if st["win_rate_pct"] is None else round(st["win_rate_pct"], 1),
        "fees": round(st["fees"], 2), "dividends": round(st["dividends"], 2),
        "positions": [
            {"symbol": s, "qty": round(p.qty, 2), "avg": round(p.avg_price, 2),
             "last": round(pf.last_prices.get(s, p.avg_price), 2), "stop": round(p.stop, 2),
             "currency": p.currency,
             "pnl_pct": round((pf.last_prices.get(s, p.avg_price) / p.avg_price - 1) * 100, 2)}
            for s, p in sorted(pf.positions.items())],
        "pending": [{"side": o.side, "qty": round(o.qty, 2), "symbol": o.symbol, "reason": o.reason}
                    for o in pf.pending],
        "last_fills": trades.tail(15).iloc[::-1].to_dict("records") if len(trades) else [],
        "curve": [[r.date, r.equity] for r in curve.itertuples()] if len(curve) else [],
        "journal": _last_journal(d / "journal.md"),
        "llm": _llm_usage(d / "llm_usage.csv"),
    }


def _last_journal(path: Path) -> dict | None:
    if not path.exists():
        return None
    parts = path.read_text().split("\n## ")
    if len(parts) < 2:
        return None
    head, *body = parts[-1].strip().splitlines()
    return {"date": head.strip(), "lines": [b[2:].replace("**", "") for b in body if b.startswith("- ")]}


def _llm_usage(path: Path) -> dict | None:
    if not path.exists():
        return None
    u = pd.read_csv(path)
    return {"calls": int(len(u)), "cost_usd": round(float(u["cost_usd"].sum()), 4),
            "tokens": int(u["input_tokens"].sum() + u["output_tokens"].sum())}


def index_curve(index: tuple | None, summaries: list[dict]) -> dict | None:
    """Index return in percent since the models' first day, on the dates we have."""
    if not index or index[1] is None or not len(index[1]):
        return None
    name, close = index
    starts = [m["curve"][0][0] for m in summaries if m["curve"]]
    if not starts:
        return None
    start = min(starts)
    close = close[[d.isoformat() >= start for d in close.index]]
    if close.empty:
        return None
    base = float(close.iloc[0])
    curve = [[d.isoformat(), round((float(v) / base - 1) * 100, 3)] for d, v in close.items()]
    return {"name": name, "return_pct": curve[-1][1], "curve": curve}


MIN_DAYS_FOR_T = 60


def excess_t_stat(model: dict, bench: dict | None) -> tuple[int, float | None]:
    """Trading days observed, and the t-statistic of daily return minus the benchmark's.

    |t| above about 2 means the difference is unlikely to be luck. With eight models, one
    will pass that bar by chance fairly often, so it is a minimum, not proof.
    """
    curve = pd.Series({d: v for d, v in model["curve"]}, dtype=float)
    days = max(len(curve) - 1, 0)
    if bench is None or model["benchmark"] or days < MIN_DAYS_FOR_T:
        return days, None
    ref = pd.Series({d: v for d, v in bench["curve"]}, dtype=float)
    diff = (curve.pct_change() - ref.pct_change()).dropna()
    if len(diff) < MIN_DAYS_FOR_T or float(diff.std()) == 0:
        return days, None
    return days, round(float(diff.mean() / (diff.std() / len(diff) ** 0.5)), 2)


def write_outputs(summaries: list[dict], state_dir: Path, docs_dir: Path,
                  starting_cash: float, base: str, errors: dict[str, str],
                  universe: dict | None = None, index: tuple | None = None,
                  data_check: dict | None = None) -> str:
    ref = next((m["return_pct"] for m in summaries if m["benchmark"]), None)
    bench = next((m for m in summaries if m["benchmark"]), None)
    for m in summaries:
        m["vs_benchmark_pct"] = None if ref is None or m["benchmark"] else round(m["return_pct"] - ref, 3)
        m["error"] = errors.get(m["key"])
        m["days"], m["t_vs_benchmark"] = excess_t_stat(m, bench)
    payload = {
        "updated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"),
        "last_date": max((m["last_date"] or "" for m in summaries), default=""),
        "starting_cash": starting_cash, "base_currency": base, "models": summaries,
        "universe": universe or {},
        "index": index_curve(index, summaries),
        "data_check": data_check and {"date": data_check["date"], "compared": data_check["compared"],
                                      "mismatched": [m[0] for m in data_check["mismatched"]]},
    }
    Path(docs_dir).mkdir(parents=True, exist_ok=True)
    (Path(docs_dir) / "data.json").write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n")

    ranked = sorted(summaries, key=lambda m: -m["return_pct"])
    lines = ["# Lekepenger: modelltesten", "",
             f"Siste børsdag behandlet: **{payload['last_date']}** · startkapital "
             f"{starting_cash:,.0f} {base} per modell".replace(",", " "), "",
             "| # | Modell | Verdi | Avkastning | Mot referanse | Største fall | Handler | Kurtasje |",
             "|---:|---|---:|---:|---:|---:|---:|---:|"]
    for i, m in enumerate(ranked, 1):
        vs = "–" if m["vs_benchmark_pct"] is None else f"{m['vs_benchmark_pct']:+.2f} %"
        lines.append(f"| {i} | {m['name']} | {m['equity']:,.0f} | {m['return_pct']:+.2f} % | {vs} "
                     f"| {m['max_drawdown_pct']:.2f} % | {m['fills']} | {m['fees']:,.0f} |".replace(",", " "))
    if payload["index"]:
        lines += ["", f"{payload['index']['name']} (indeks, med utbytte) i samme periode: "
                      f"**{payload['index']['return_pct']:+.2f} %**"]
    for m in summaries:
        lines += ["", f"## {m['name']}", "", m["blurb"], ""]
        if m["error"]:
            lines += [f"**Feil ved siste kjøring:** {m['error']}", ""]
        if m["positions"]:
            lines += ["| Aksje | Antall | Snitt | Sist | Stop | +/- % |", "|---|---:|---:|---:|---:|---:|"]
            lines += [f"| {p['symbol']} | {p['qty']} | {p['avg']:.2f} | {p['last']:.2f} | "
                      f"{p['stop'] if p['stop'] else '–'} | {p['pnl_pct']:+.1f} |" for p in m["positions"]]
        else:
            lines.append("Ingen posisjoner.")
        if m["pending"]:
            lines += ["", "Ordre til neste åpning: " + ", ".join(
                f"{'kjøp' if o['side'] == 'BUY' else 'selg'} {o['qty']} {o['symbol']}" for o in m["pending"])]
    text = "\n".join(lines) + "\n"
    Path(state_dir).mkdir(parents=True, exist_ok=True)
    (Path(state_dir) / "REPORT.md").write_text(text)
    return text
