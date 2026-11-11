# AI Trading Engine — nine play-money models

Nine trading models compete with 100,000 play-money NOK each on Nordic shares.
The universe is every share in `config/universe.csv` (278 Oslo listings plus about 80
large companies in Stockholm, Copenhagen and Helsinki); each run only
allows new buys in shares with a median daily turnover of at least 1 MNOK over the last
60 days and a price of at least 1 NOK, and never in shares with a one-day move above
50 % in the last year (see `universe:` in `config.yaml`).
A GitHub Actions job advances all of them once per trading day and publishes a dashboard.

Nothing here trades real money, and nothing here is a validated strategy or investment advice.

| Key | Model | Rule |
|---|---|---|
| `referanse` | Benchmark | buy and hold every tradable share, equal weight, no costs |
| `trend` | Trend | buy uptrends, ATR trailing stop |
| `momentum` | Momentum rotation | hold the 10 strongest by 12-1 momentum, re-rank monthly |
| `momentumbrems` | Momentum with market brake | as `momentum`, but sells a market's shares while that market's index is below its 200-day average |
| `tilbakefall` | Mean reversion | buy sharp dips in shares that are in an uptrend |
| `sentiment` | Trend + sentiment score | needs rows in `signals/scores.csv`; in cash until then |
| `sprakmodell` | Trend + LLM score | needs rows in `signals/llm_scores.csv`; in cash until then |
| `scoretopp` | Score rotation | hold the 10 shares with the highest aksjescore, re-rank monthly |
| `dagshandel` | Day trading | buy at the open, sell everything at the same close |

Models are defined in `app/models.py`; adding one is a new entry there plus a strategy
class with a `signals(ctx)` method in `app/strategy.py`.

## Run it

```bash
pip install -r requirements-dev.txt
python -m pytest -q
python -m app.run_daily                          # advance all models one step
python -m app.run_daily --backtest 2024-01-01    # dry run from a date, state untouched
```

For every new *completed* daily bar, each model:

0. applies splits and credits dividends for shares held since before the ex-date
1. fills orders queued on the previous bar at the open (slippage + commission applied)
2. triggers stops against the day's low (a gap through the stop fills at the open)
3. marks to market and records equity
4. produces signals and queues orders for the next open

Decisions are made on the close and filled at the next open, so there is no look-ahead.
A date is processed once per model; running the job twice does nothing the second time.
All models see the same prices and pay the same costs, so their results are comparable.

| Path | Role |
|---|---|
| `app/engine.py` | daily loop (shared by live run and backtest) |
| `app/models.py` | the seven models |
| `app/strategy.py` | strategy rules and `ScoreBook` (external AI scores) |
| `app/settings.py` | loads `config.yaml`, enforces stricter-only model rules |
| `app/llm_agent.py` | language-model agent (scores, journal, cost) |
| `app/risk.py` | position sizing, open-risk cap, max positions, daily loss halt |
| `app/sim_broker.py` | fills, commission, slippage, cash accounting |
| `app/data/providers.py` | Yahoo (yfinance) and CSV price data |
| `config/universe.csv` | instruments; `enabled=true` rows are traded |
| `state/<model>/` | `portfolio.json`, `trades.csv`, `equity.csv` |
| `state/REPORT.md` | combined report |
| `docs/` | dashboard (GitHub Pages): `index.html` reads `data.json` |

## Schedule

`.github/workflows/paper-trade.yml` runs the tests and then the daily step every weekday
at 17:15 UTC (after the Oslo close in both summer and winter time), commits `state/`
and `docs/data.json`, and mirrors `docs/` to the `gh-pages` branch, which serves the
dashboard at https://antennmast.github.io/ai-trading-engine/. Scheduled runs can start late, and GitHub pauses schedules in
repositories with no activity for 60 days; the engine catches up on missed days. A failed
run opens an issue titled "Daglig kjøring feilet".

## Settings

Everything is in `config.yaml`: starting cash, shared risk rules, costs, the liquidity
filter and the language-model agent. Under `models:` a model can override a risk rule, but
only to something stricter; a looser value stops the run with an error.

## Where the AI goes

- **`sentiment`** reads `signals/scores.csv`. With the `AKSJESCORE_TOKEN` secret set, the
  workflow imports the daily aksjescore (0-100 becomes -1..1) before the models run.
- **`sprakmodell`** reads `signals/llm_scores.csv`, written by `app/llm_agent.py`. With the
  `ANTHROPIC_API_KEY` secret set, the agent sends the day's trend candidates (price
  statistics, plus news headlines when aksjescore is available) to the model in
  `config.yaml`, three times per trading day, and uses the median score. It logs its reasons in
  `state/sprakmodell/journal.md` and tokens and cost in `state/sprakmodell/llm_usage.csv`.

A score can veto a buy or force an exit; it can never change position size or bypass a
risk rule. Without the secrets both models simply stay in cash.

## Known limitations

- Yahoo data is unofficial, delayed and occasionally wrong or unavailable.
- Dividends are credited on the ex-date without withholding tax, and Yahoo's dividend
  amounts are occasionally in another currency than the share. Splits adjust quantity,
  average price and stop. A held share with no prices for 10 days is closed at its last
  price, which only approximates a takeover or delisting. Rights issues and spin-offs are
  not handled.
- Cash is NOK only. Foreign shares are converted at the day's FX rate, so currency moves
  are part of each model's result. aksjescore only covers Oslo, so `sentiment` only buys there.
- OSEBX on the dashboard is a Norwegian total-return index; the models also hold other
  Nordic shares and ignore dividends, so it is a yardstick, not a like-for-like benchmark.
- Daily bars only. Fills assume the full quantity trades at the open (or close).
- The research behind the design choices is summarised in `FORSKNING.md`.
- Rules are frozen during the test; changes are recorded in `ENDRINGSLOGG.md`.
- With nine models, the best one after a few months may simply have been lucky.

---

# Part B — IBKR Paper Trading connector (optional, runs on your own machine)

A deliberately conservative first implementation for connecting Python to an IBKR
Paper Trading session. It reads account summary, qualifies one stock contract and
can request historical bars. It does **not** run an autonomous strategy and does
not submit orders unless the explicit test-order command is used.

## V1 decisions

- Capital reference: NOK 100,000
- Universe: Nordic equities (Norway, Sweden, Denmark, Finland)
- Style: swing trading, with intraday monitoring
- Long-only; no leverage; no short selling
- Paper Trading only
- Initial risk settings are in `.env.example`; they are not validated performance claims.

## 1. Requirements

- Python 3.11+
- IBKR account with Paper Trading enabled
- IB Gateway or Trader Workstation logged into the **Paper Trading** account
- TWS API socket access enabled
- Market-data permissions appropriate for the instruments you request

Install:

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt -r requirements-ibkr.txt
cp .env.example .env
```

Use the API package version supported by the installed TWS / IB Gateway version.
If `ibapi` is not available from PyPI in your environment, install the Python API
package from the official IBKR TWS API download.

## 2. IBKR setup

For IB Gateway Paper Trading, the common default socket port is `4002`.
For TWS Paper Trading, the common default is `7497`.

In TWS/IB Gateway:
1. Log in to Paper Trading.
2. Enable socket/API clients.
3. Confirm the socket port matches `.env`.
4. Keep API access restricted to localhost.
5. Do not expose the API socket to the public internet.

The program additionally refuses to start unless `IBKR_PAPER_ONLY=true` and the
configured port is one of the standard paper ports (4002 or 7497). This is a
guardrail, not proof of account type; verify the Paper Trading login in the GUI.

## 3. First connection test

```bash
python -m app.main --account
```

This connects, prints account summary values, and disconnects.

Test a contract and retrieve historical daily bars:

```bash
python -m app.main --history --symbol EQNR --currency NOK --primary-exchange OSE
```

The example symbol/exchange is a starting point only. Contract availability,
routing and market-data permissions must be confirmed in IBKR Contract Details.
For other Nordic listings, set the correct symbol, currency and primary exchange
after verifying the exact listing in TWS.

## 4. Explicit paper test order

Order submission is disabled by default. To submit a small limit order to the
Paper Trading account, explicitly set:

```dotenv
ALLOW_PAPER_TEST_ORDERS=true
```

Then run with an instrument you have verified and a deliberately conservative
limit price / quantity:

```bash
python -m app.main --paper-limit-order \
  --symbol EQNR --currency NOK --primary-exchange OSE \
  --quantity 1 --limit-price 1.00
```

The example price is intentionally unlikely to execute; it demonstrates order
submission and status callbacks. Never use this command against a live session.
The application checks the paper-only port and paper-only flag but cannot
independently prove which account is logged into the GUI.

## 5. Nordic contract mapping

Use `SMART` for routing and specify `primaryExchange` to disambiguate the listing.
Confirm the exact IBKR exchange code in Contract Details before adding a symbol.
Do not assume the venue code from the local exchange brand name.

Suggested configuration fields:
- Norway: currency NOK; verify primary exchange code in IBKR
- Sweden: currency SEK; verify primary exchange code in IBKR
- Denmark: currency DKK; verify primary exchange code in IBKR
- Finland: currency EUR; verify primary exchange code in IBKR

Maintain a curated instrument master with `symbol`, `currency`, `primary_exchange`,
`con_id`, `min_tick`, `lot_size`, `trading_hours`, and `eligible=true/false`.
Prefer `con_id` after qualification to avoid ambiguous contracts.

## 6. Market data and OSE caveat

IBKR market data availability depends on account permissions and subscriptions.
IBKR currently documents OSE API streaming data as 15-minute delayed. Confirm
the live/delayed status returned by IBKR before designing intraday execution
around Norwegian equities. Do not treat delayed quotes as live execution prices.

## 7. What is deliberately not implemented yet

- Live trading
- Routing the simulator's orders to IBKR (the simulator in Part A keeps its own book)
- Automatic universe discovery
- Stop / bracket orders at IBKR, reconnect orchestration, order reconciliation
- Production-grade monitoring

These must be added and tested in later milestones. Do not deploy this scaffold
as an unattended live trading system.
