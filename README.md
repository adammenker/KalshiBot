# KalshiBot

Small Python starter project for authenticated Kalshi API requests.

## Setup

```bash
cd /Users/adammenker/workplace/KalshiBot
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
cp .env.example .env
```

Create an API key in Kalshi demo or production, save the downloaded private key somewhere outside git, and update `.env`.

Demo keys only work with demo endpoints. Production keys only work with production endpoints.

## Smoke Test

After filling in `.env`, run:

```bash
kalshibot balance
```

Or without installing the console script:

```bash
python -m kalshibot.cli balance
```

## Polymarket Read-Only Market Data

Polymarket market discovery uses the public Gamma API. Orderbook and pricing use the public CLOB API; no Polymarket auth is required for these read-only calls.

Fetch a market by slug and list its outcome token IDs:

```bash
kalshibot poly-market fed-decision-in-october
```

List active events and their market token IDs:

```bash
kalshibot poly-events --limit 5
```

Fetch top-of-book executable prices for one Polymarket token:

```bash
kalshibot poly-book <token_id>
```

For spread detection, prefer `poly-book`: `best_ask` is the executable buy price and `best_bid` is the executable sell price.

## Market Title Matching

Market discovery can use a local LLM to decide whether a Polymarket title and a Kalshi title describe the same tradable contract. The matcher is strict: same event is not enough; outcome/side, date or period, threshold, and settlement condition should also match.

The default local backend expects an Ollama-compatible API:

```bash
ollama pull llama3.1:8b
ollama serve
```

Then compare two titles:

```bash
kalshibot match-titles \
  --polymarket-title "Spain vs Saudi Arabia - Spain wins" \
  --kalshi-title "World Cup game: Spain wins vs Saudi Arabia"
```

If the local model is unavailable or returns invalid JSON, the standalone `match-titles` command falls back to a conservative lexical matcher. Discovery does not use this fallback for final approval.

Run one-time market discovery by pulling Kalshi markets, generating canonical search queries, searching Polymarket, structurally scoring candidates, and then using the local matcher only as a final verifier:

```bash
pip install -e '.[discovery]'
```

```bash
kalshibot discover-matches \
  --kalshi-limit 5
```

By default, discovery uses Polymarket's `public-search` endpoint with multiple generated queries per Kalshi market, not just the raw Kalshi title. The CLI defaults to the project's current `--market-profile win-lose`, `--strategy polymarket-search`, `--kalshi-limit 5`, `--kalshi-sort-by volume-24h`, `--review-output logs/discovery_matches.json`, and `--pairs-output config/approved_market_pairs.json`. Use `--market-profile sports-game-winner`, `--market-profile crypto-threshold`, `--market-profile event-winner`, `--market-profile economic-release`, or `--market-profile general` for targeted passes.

Discovery separates equivalence from signal. Price validation defaults to `--price-validation-mode warn`, so Kalshi/Polymarket price gaps are recorded as diagnostics instead of hard-rejecting otherwise equivalent contracts. Use `--price-validation-mode reject` only for a special sanity-check run. The hard blockers are structural: threshold mismatch, date/deadline mismatch, comparator mismatch, entity mismatch, side ambiguity, materially different settlement timing/source, and similar contract-rule differences.

Discovery stdout is intentionally compact: matched Kalshi/Polymarket titles first, then a small JSON stats block with candidate counts and filter hits. By default the CLI drops matches whose contract date is before today; use `--include-past-contracts` for historical/manual diagnostics or `--min-match-date YYYY-MM-DD` to set the cutoff explicitly. Some profiles also apply a future-date window: sports game-winner discovery defaults to the next 14 days, crypto thresholds to 60 days, and economic releases to 90 days, while long-horizon event/election-style profiles stay uncapped. Use `--max-match-date YYYY-MM-DD` to override the profile window or `--no-max-match-date` to disable it. `--review-output` writes a compact JSON match review file with only confidence/side, Kalshi title/date/midpoint, and Polymarket title/date/midpoint. `--approved-review-output` writes the same compact shape, but only for pairs promoted into heartbeat. The main discovery JSON is compact by default and contains `summary` plus kept `matches`; use `--diagnostics-output`, `--rejected-output`, or `--search-debug-output` only when you need bulky per-result diagnostics, including generated queries, normalized fields, structural score, side mapping, blocking issues, LLM result, and price-gap metadata. `--pairs-output` writes machine-approved generated pairs with condition IDs, selected/sibling token IDs, side mapping, category, confidence, notes, blockers, and normalized fields for heartbeat.

Polymarket search can return deeply nested event trees, especially for sports games with winner, spread, total, and prop markets. Serious discovery now defaults to a higher event contract cap and relies more on candidate-level structural filtering. Use a low `--max-polymarket-contracts-per-event` only for fast diagnostics when you intentionally want to skip large nested events.

For sports runs, `--market-profile sports-game-winner` targets literal game-winner markets and rejects spreads, totals, player props, season win totals, and wrong-date duplicate matchups. When you do want a targeted run, add `--kalshi-include-series` so discovery queries each desired Kalshi series directly; this prevents rare series from being missed just because they do not appear on the first broad markets page.

The older broad scan is still available for diagnostics:

```bash
kalshibot discover-matches \
  --strategy broad \
  --polymarket-event-limit 25 \
  --kalshi-pages 1 \
  --index-path data/kalshi_market_index
```

In broad mode, discovery uses semantic search over Kalshi titles when `sentence-transformers` is installed. The Kalshi embedding index is persisted at `--index-path` and incrementally adds newly seen tickers on later runs. If the optional discovery dependencies are unavailable, broad discovery falls back to the older lexical scorer.

You can also promote an existing discovery file without rerunning API discovery:

```bash
kalshibot promote-discovered-matches \
  --input data/discovered_market_matches.json \
  --output config/approved_market_pairs.json \
  --min-confidence 0.90
```

Promotion is stricter than discovery: by default it only writes matches whose discovery price validation passed, so price-warning/manual-review matches do not enter heartbeat automatically. Use `--include-price-warnings` only when you intentionally want heartbeat to try those pairs.

Optional environment variables:

```bash
LOCAL_LLM_BASE_URL=http://localhost:11434
LOCAL_LLM_MODEL=llama3.1:8b
LOCAL_LLM_TIMEOUT_SECONDS=30
```

## Spread Checks

Compare executable buy prices between an equivalent Kalshi market and a Polymarket token:

```bash
kalshibot spread-check \
  --kalshi-ticker KALSHI-MARKET-TICKER \
  --polymarket-token-id POLYMARKET_YES_CLOB_TOKEN_ID \
  --outcome yes
```

Default filters require:

- Kalshi is cheaper than Polymarket.
- Each venue's bid/ask spread is at most `0.05`.
- Each venue has at least `10` contracts available at the executable buy price.
- Each venue has at least `50` contracts available within `0.03` of the executable buy price.
- Fee-adjusted edge is at least `0.01` after the configured Kalshi fee model. The default is entry-only fees for hold-to-resolution testing.

Tune those with `--max-venue-spread`, `--min-buy-size`, `--min-depth-size`, `--depth-window`, `--min-edge`, and `--min-fee-adjusted-edge`.

Or maintain manual market mappings in JSON:

```bash
cp config/market_pairs.example.json config/market_pairs.json
kalshibot spread-check --pairs config/market_pairs.json
```

The output field `polymarket_minus_kalshi` is positive when Polymarket's executable buy price is higher than Kalshi's executable buy price. `fee_adjusted_edge` subtracts the configured Kalshi fee model from that raw edge. `--fee-mode entry-only` is the default and subtracts only the estimated buy fee for hold-to-resolution EV testing. `--fee-mode round-trip` subtracts estimated entry and exit taker fees for pre-resolution convergence trading. `kalshi_lower: true` means the rough paper-trade signal you described is present before fees, slippage, and resolution-rule checks.

## Heartbeat Recorder

Record repeated spread observations to SQLite while fetching Kalshi and Polymarket orderbooks concurrently:

```bash
kalshibot heartbeat \
  --iterations 12 \
  --interval-seconds 5
```

Use `--iterations 0` to run continuously. `--interval-seconds` accepts decimals like `0.5`, and `--interval-ms 500` is equivalent. By default heartbeat reads `config/approved_market_pairs.json`, writes `data/observations.sqlite`, uses fixed-rate batch scheduling, compact summary stdout, entry-only fees, hold-to-resolution paper trades, batched SQLite writes, and refreshes non-orderbook Polymarket metadata every 5 seconds. Each batch checks all active pairs concurrently, and each pair fetches Kalshi and Polymarket orderbooks concurrently. If a pair fails repeatedly, for example because a market closed or no longer has an orderbook, heartbeat drops it from the active runtime list after `--drop-failed-pairs-after` consecutive failures. The default is `3`; use `0` to keep retrying failed pairs.

Performance knobs:

- `--heartbeat-output summary|quiet|full`: `summary` prints one compact JSON line per tick, `quiet` prints nothing, and `full` restores detailed per-market JSON.
- `--scheduler fixed-rate|sleep-after-batch|per-market`: `fixed-rate` starts each batch on the requested cadence when possible, `sleep-after-batch` preserves the old loop behavior, and `per-market` gives each market its own independent cadence.
- `--metadata-refresh-seconds N`: orderbooks refresh every tick, while Polymarket Gamma/Data metadata such as volume/OI refreshes every `N` seconds. Use `0` to fetch metadata every tick.

Fast live-game EV-style run:

```bash
kalshibot heartbeat \
  --iterations 0 \
  --interval-ms 250 \
  --min-fee-adjusted-edge 0.01 \
  --drop-failed-pairs-after 0
```

Each observation records request start/receive timestamps, per-venue latency, response skew, prices, spread, estimated Kalshi fees, fee-adjusted edge, depth, filter results, helpful market URLs, and the raw normalized JSON. When a row passes the spread/liquidity filters, the bot writes a paper signal and opens a conservative paper trade if there is not already an open trade for the same market.

For Polymarket pairs with `polymarket_condition_id` in the config, the heartbeat also records Polymarket open interest from the Data API. It also stores Polymarket volume from Gamma market metadata, Kalshi/Polymarket mid prices, mid-price deltas over the lookback window, OI deltas, and volume deltas. This helps separate a stale/insignificant gap from a spread that appears alongside fresh market participation.

The signal filter supports the strategy shape:

- Polymarket mid minus Kalshi mid is above `--min-mid-edge`.
- Polymarket mid increased by at least `--min-poly-mid-move` over `--signal-lookback-minutes`.
- Polymarket OI increased by at least `--min-poly-oi-delta`.
- Polymarket volume increased by at least `--min-poly-volume-delta` over the lookback window.
- Kalshi mid has not moved more than `--max-kalshi-mid-move`.
- Kalshi top-of-book size/depth and venue spread filters still pass. Polymarket depth is recorded for analysis, but it does not block a paper signal because Polymarket is read-only in this strategy.

The OI/momentum defaults are permissive so the collector keeps accumulating data, but the spread gate now requires at least `0.01` after the configured Kalshi fee model. Example stricter run:

```bash
kalshibot heartbeat \
  --iterations 0 \
  --interval-seconds 5 \
  --min-fee-adjusted-edge 0.02 \
  --min-mid-edge 0.05 \
  --min-poly-mid-move 0.03 \
  --min-poly-oi-delta 1 \
  --min-poly-volume-delta 1 \
  --max-kalshi-mid-move 0.01
```

For a hold-to-resolution EV test, entry-only fees and no edge-convergence exit are already the defaults:

```bash
kalshibot heartbeat \
  --iterations 0 \
  --interval-ms 500 \
  --min-fee-adjusted-edge 0.01
```

Paper trades simulate buying the configured target size on Kalshi at the observed executable depth-adjusted fill price. Later observations for the same market mark the open trade at Kalshi's current executable sell price, tracking latest unrealized PnL, best unrealized PnL, worst unrealized PnL, latest edge, and observation count. They also track `hold_to_resolution_ev`, estimated as `(latest Polymarket midpoint - Kalshi purchase price) * quantity - entry fee`, so hold-to-resolution tests can be evaluated separately from immediate liquidation PnL. Paper liquidation PnL fields are net of estimated Kalshi entry and exit taker fees; gross PnL and fee fields are stored separately for auditability.

Paper trades can also close before event resolution. In market terminology, this is exiting the position by selling the same contract before settlement. Pre-resolution edge exits are disabled by default for hold-to-resolution testing. Enable them with `--paper-exit-edge 0` or another threshold, or add optional risk controls with `--paper-take-profit`, `--paper-stop-loss`, and `--paper-max-hold-minutes`.

By default, paper trade lifecycle events are also written outside SQLite:

- `logs/paper_trades.jsonl`: append-only JSONL rows when a paper trade opens or closes, including market, URLs, purchase price, sell price, fees, liquidation PnL, and hold-to-resolution EV.
- `logs/paper_pnl.json`: a current paper PnL snapshot rewritten whenever the trade log receives a new open/close event.

Override these with `--paper-trade-log` and `--paper-pnl-log`, or disable both files with `--no-paper-logs`.

The SQLite tables are:

- `observations`: every heartbeat comparison.
- `paper_signals`: raw signal rows created from observations that passed the filters.
- `paper_trades`: paper trade lifecycle state, including close reason, exit price, realized PnL when a trade is closed, and latest hold-to-resolution EV.
- `paper_trade_marks`: mark-to-market and hold-to-resolution EV history for open paper trades.

Summarize the recorded observations:

```bash
kalshibot analyze --db data/observations.sqlite
```

The analysis report includes observation counts, paper signal counts, paper trade counts/PnL, edge stats, Polymarket OI stats, latency/skew stats, which venue responded first, filter reason counts, and per-market summaries.

## Historical Backtesting

Historical backtesting uses Kalshi candlesticks and Polymarket price history. This is useful for fast strategy iteration, but it is less precise than the live heartbeat recorder because it does not reconstruct full historical orderbook depth or latency.

Backfill historical prices for a mapped pair file:

```bash
kalshibot backfill-history \
  --pairs config/spain_saudi_pairs.json \
  --db data/history.sqlite \
  --start 2026-06-20T00:00:00Z \
  --end 2026-06-20T12:00:00Z
```

Run the simple historical benchmark:

```bash
kalshibot backtest-history \
  --db data/history.sqlite \
  --min-edge 0.02 \
  --hold-period-minutes 10 \
  --slippage 0.005
```

The first benchmark enters when Polymarket's historical price is at least `--min-edge` above Kalshi's historical YES ask. It exits when the spread closes, the hold period expires, or the data ends. PnL is conservative: entry uses Kalshi ask plus slippage, and exit uses Kalshi bid minus slippage.

Historical SQLite tables are:

- `historical_prices`: raw Kalshi and Polymarket history.
- `historical_aligned_prices`: timestamp-aligned pair prices and spreads.
- `backtest_runs`: aggregate run metrics.
- `backtest_trades`: simulated historical trades.

## Code Layout

- `auth.py`, `client.py`, `config.py`: Kalshi auth, HTTP client, and environment setup.
- `polymarket.py`: read-only Polymarket Gamma, CLOB, and Data API helpers.
- `market_matcher.py`: local-LLM matching plus a standalone conservative title-check fallback.
- `discovery/`: market discovery sources, candidate ranking, semantic indexes, taxonomy, filters, profiles, and debug flow summaries.
- `commands/`: CLI command groups for discovery, Polymarket utilities, trading, and history/backtesting.
- `spreads.py`: market-pair config, orderbook parsing, spread/depth/mid-price calculations.
- `monitoring/`: realtime heartbeat orchestration, concurrent fetches, observation persistence, and output formatting.
- `signals.py`: signal scoring from mid-price, OI, volume, and Kalshi movement filters.
- `storage.py`: SQLite schema creation and lightweight migrations.
- `paper.py`: paper signal/trade lifecycle and mark-to-market updates.
- `analysis.py`: SQLite reporting and per-market summaries.
- `backtesting/`: historical backfill alignment and simple backtest simulation.
- `monitor.py`, `backtest.py`: compatibility facades for older imports.
- `cli.py`: command-line interface wiring.

## Environment Variables

```bash
KALSHI_ENV=demo
KALSHI_API_KEY_ID=your-api-key-id
KALSHI_PRIVATE_KEY_PATH=/absolute/path/to/kalshi-private-key.key
```

Supported `KALSHI_ENV` values:

- `demo`: `https://external-api.demo.kalshi.co/trade-api/v2`
- `prod`: `https://external-api.kalshi.com/trade-api/v2`

You can override the base URL with `KALSHI_BASE_URL` if needed.

## What This Includes

- RSA-PSS/SHA256 signing for authenticated requests.
- Demo/production environment configuration.
- A tiny `KalshiClient` wrapper around `requests`.
- A read-only `PolymarketClient` for Gamma market discovery and CLOB orderbook prices.
- A spread checker for manually mapped equivalent markets.
- A heartbeat recorder that stores spread observations, paper signals, and paper trade lifecycle data in SQLite.
- A historical price backfill and backtest workflow for quick strategy benchmarking.

This is intentionally small, so the next step can be adding market-data scanning or order placement with tests around the strategy logic.
