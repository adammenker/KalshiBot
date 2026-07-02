Implement a Strategy Variant Engine for the existing KalshiBot project.

Context:
Discovery is now working and has successfully matched a couple Kalshi/Polymarket markets. Do not spend this iteration debugging discovery. The current issue is that our basic strategy is too narrow:

Current basic strategy:

* Polymarket odds increase.
* Assume Polymarket is a useful fair-value heuristic.
* If Kalshi has not repriced yet, buy the matching Kalshi contract.
* Hold the Kalshi contract to resolution to avoid exit fees.
* Use entry-only fee-adjusted EV.

This is a good baseline, but it is likely too close to a pure latency race. The next goal is not to immediately add strict filters or advanced ML. The goal is to turn the existing bot into an experiment engine that can run multiple paper/shadow strategy variants in parallel over the same heartbeat observations.

The project already has:

* Kalshi API client.
* Polymarket read-only Gamma/CLOB/Data API helpers.
* Discovery and generated pair configs.
* Spread checks.
* Heartbeat observation recorder.
* OI/volume/mid-delta fields.
* Paper signals.
* Paper trades.
* Paper trade marks.
* Hold-to-resolution EV using Polymarket midpoint.
* Analysis command.
* Historical backtest workflow.

Build on that. Do not rewrite the whole bot.

Primary objective:
Create a Strategy Variant Engine that evaluates multiple strategy variants on every heartbeat observation and records their outputs separately. This should let us compare different approaches without constantly changing the heartbeat filters.

High-level design:
Current flow is roughly:

```text
approved pairs → heartbeat observation → filter reasons → paper signal → paper trade
```

New flow should become:

```text
approved pairs
  → heartbeat observation
  → strategy variant engine
  → strategy signals / shadow signals
  → optional paper trades
  → marks and analysis by strategy_id
```

Important:
For now, we want more observations and more paper/shadow signals, not fewer. Do not make OI, volume, flow, or depth stricter requirements for all variants. Log them as features. Some variants can use them, but baseline scout variants should be permissive.

Definitions:

1. Observation
   Existing heartbeat row. This is the source data.

2. Strategy variant
   A named strategy evaluator that receives the current observation/check plus historical context and returns one of:

* no signal
* shadow signal only
* paper trade signal
* close/mark instruction for existing paper trade, if applicable

3. Shadow signal
   A recorded candidate event that is not necessarily tradeable. The point is to collect labels and learn what would have happened.

Examples:

* Polymarket moved +1c while Kalshi did not.
* Polymarket-Kalshi mid gap widened.
* Fee-adjusted edge was negative but close.
* Kalshi depth was too thin.
* OI was flat but price moved.
* A NO-side opportunity appeared.

Shadow signals should be recorded even when they would not be a real-money trade.

4. Paper trade signal
   A variant-approved signal that opens or updates a simulated paper trade.

5. Strategy run
   A heartbeat/session/run id plus a set of strategy variants and parameters.

Core implementation goal:
Add an extensible strategy system without breaking existing heartbeat behavior.

Proposed files:

```text
src/kalshibot/strategies/
  __init__.py
  base.py
  context.py
  engine.py
  registry.py
  variants.py
  fair_value.py
  passive.py
```

Possible existing files to integrate:

* `signals.py`
* `paper.py`
* `storage.py`
* `monitoring/heartbeat.py`
* `monitoring/observations.py`
* `analysis.py`

Do not force this exact layout if the repo structure suggests a cleaner integration, but keep strategy logic modular.

Core data classes:

```python
@dataclass(frozen=True)
class StrategyContext:
    connection: sqlite3.Connection
    run_id: str
    observed_at: str
    observation_id: int
    check: SpreadCheck
    metrics: dict[str, str | None]
    history: Sequence[Mapping[str, Any]]
    config: StrategyEngineConfig
```

```python
@dataclass(frozen=True)
class StrategyDecision:
    strategy_id: str
    strategy_version: str
    signal_type: str  # "none", "shadow", "paper_open", "paper_close", "mark_only"
    side: str         # "yes" or "no"
    direction: str    # "buy_yes", "buy_no", "sell_yes", etc.
    confidence: Decimal | None
    score: Decimal | None
    fair_value: Decimal | None
    entry_price: Decimal | None
    edge: Decimal | None
    fee_adjusted_edge: Decimal | None
    reasons: tuple[str, ...]
    rejection_reasons: tuple[str, ...]
    metadata: dict[str, Any]
```

```python
@dataclass(frozen=True)
class StrategyVariant:
    strategy_id: str
    strategy_version: str

    def evaluate(self, context: StrategyContext) -> StrategyDecision:
        ...
```

If protocol-style interfaces are cleaner, use `typing.Protocol`.

Storage changes:

Add new tables. Keep existing `observations`, `paper_signals`, `paper_trades`, and `paper_trade_marks` working.

New table: `strategy_signals`

Purpose:
Store every strategy decision that is not `"none"`, including shadow signals and paper-open signals.

Suggested schema:

```sql
CREATE TABLE IF NOT EXISTS strategy_signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    observation_id INTEGER NOT NULL,
    run_id TEXT NOT NULL,
    observed_at TEXT NOT NULL,

    strategy_id TEXT NOT NULL,
    strategy_version TEXT NOT NULL,
    signal_type TEXT NOT NULL,

    label TEXT,
    outcome TEXT,
    kalshi_ticker TEXT NOT NULL,
    polymarket_token_id TEXT NOT NULL,
    polymarket_condition_id TEXT,

    side TEXT,
    direction TEXT,

    score TEXT,
    confidence TEXT,

    fair_value TEXT,
    entry_price TEXT,
    mark_price TEXT,
    edge TEXT,
    fee_adjusted_edge TEXT,

    kalshi_buy_price TEXT,
    kalshi_sell_price TEXT,
    polymarket_buy_price TEXT,
    polymarket_mid_price TEXT,
    kalshi_mid_price TEXT,
    polymarket_mid_minus_kalshi_mid TEXT,

    polymarket_mid_delta TEXT,
    kalshi_mid_delta TEXT,
    polymarket_open_interest TEXT,
    polymarket_open_interest_delta TEXT,
    polymarket_volume TEXT,
    polymarket_volume_delta TEXT,

    reasons_json TEXT NOT NULL,
    rejection_reasons_json TEXT NOT NULL,
    metadata_json TEXT NOT NULL,

    created_at TEXT NOT NULL
);
```

New table: `strategy_paper_trades`, or extend existing `paper_trades`.

Preferred path:
Extend existing `paper_trades` with nullable columns:

* `strategy_id`
* `strategy_version`
* `strategy_signal_id`
* `fair_value_provider`
* `entry_policy`
* `exit_policy`
* `side`
* `direction`

If changing existing table is annoying, create a separate mapping table:

```sql
CREATE TABLE IF NOT EXISTS strategy_paper_trade_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy_signal_id INTEGER NOT NULL,
    paper_trade_id INTEGER NOT NULL,
    strategy_id TEXT NOT NULL,
    strategy_version TEXT NOT NULL,
    created_at TEXT NOT NULL
);
```

Do whichever is cleaner with the current migrations.

Critical behavior:
Multiple strategies must be able to create separate paper trades for the same market at the same time. The existing `open_paper_trade_exists` currently prevents duplicate open trades for the same label/outcome/ticker/token. That is good for one strategy, but it blocks multi-strategy experiments.

Modify open-trade uniqueness so it includes strategy identity.

Current uniqueness concept:

```text
label + outcome + kalshi_ticker + polymarket_token_id
```

New uniqueness concept:

```text
strategy_id + strategy_version + label + outcome + kalshi_ticker + polymarket_token_id + direction
```

This lets us compare:

* baseline taker strategy
* persistent gap strategy
* passive simulation strategy
* NO-side strategy

on the same market at the same time.

Strategy engine behavior:

Add an engine that runs all enabled variants on each observation.

Pseudo-code:

```python
def evaluate_strategies(context: StrategyContext, variants: list[StrategyVariant]) -> list[StrategyDecision]:
    decisions = []

    for variant in variants:
        try:
            decision = variant.evaluate(context)
        except Exception as exc:
            decision = StrategyDecision(
                strategy_id=variant.strategy_id,
                strategy_version=variant.strategy_version,
                signal_type="shadow",
                side=context.check.outcome,
                direction="error",
                confidence=None,
                score=None,
                fair_value=None,
                entry_price=None,
                edge=None,
                fee_adjusted_edge=None,
                reasons=("strategy_error",),
                rejection_reasons=(type(exc).__name__,),
                metadata={"error": str(exc)},
            )

        if decision.signal_type != "none":
            decisions.append(decision)

    return decisions
```

Hook this into heartbeat after each observation is inserted and after existing metrics are computed.

Do not break existing paper signal/trade behavior. Either:

1. keep the existing default behavior as a strategy variant named `legacy_fee_adjusted_edge`, or
2. keep it separately and add the new engine alongside it.

Preferred:
Convert the legacy logic into a variant so analysis can compare it against new variants.

Strategy variants to implement in the first iteration:

Variant 1: `legacy_fee_adjusted_edge`

Purpose:
Reproduce current behavior.

Logic:

* Uses current SpreadCheck filters.
* Requires fee-adjusted edge above configured threshold.
* Opens paper trade if it passes.
* Uses existing hold-to-resolution EV logic.
* This should match current behavior as closely as possible.

Signal type:

* `paper_open` when current filters pass
* optionally `shadow` when it nearly passes, depending on config

Variant 2: `loose_poly_lead_scout`

Purpose:
Create more candidate data. This is not a real-money strategy. It is a scout/shadow strategy.

Logic:

* If Polymarket mid moved more than a small threshold over lookback window, record a shadow signal.
* Do not require fee-adjusted edge.
* Do not require OI.
* Do not require volume.
* Do not require positive depth except both venues must have usable market data.
* Track whether Kalshi moved less than Polymarket.

Suggested defaults:

* `min_abs_poly_mid_move = 0.01`
* `min_poly_minus_kalshi_move_advantage = 0.005`
* `lookback_minutes = existing heartbeat lookback`

Signal type:

* `shadow`

Metadata:

* poly_mid_delta
* kalshi_mid_delta
* poly_lead_amount = poly_mid_delta - kalshi_mid_delta
* current mid spread
* current executable spread
* fee-adjusted edge
* OI delta
* volume delta
* filter reasons

This variant is for learning, not trading.

Variant 3: `top_n_poly_lead_paper`

Purpose:
Force exploratory paper trades when the bot is otherwise too quiet.

Logic:

* Rank candidate signals per heartbeat/run window by score.
* Open paper trades for top N strongest signals per day or per run.
* This can be implemented later if per-day ranking is harder; for first pass, open paper trades when score exceeds a permissive threshold.

Suggested score:

```text
score =
  abs(poly_mid_delta) * 100
+ max(0, poly_mid_delta - kalshi_mid_delta) * 100
+ max(0, polymarket_mid_minus_kalshi_mid) * 50
+ max(0, fee_adjusted_edge) * 100
```

Do not block on OI/volume. Include them only as metadata.

Signal type:

* `paper_open` if score >= configured threshold
* otherwise `shadow`

Variant 4: `persistent_mid_gap`

Purpose:
Test non-instant spread opportunities that persist rather than only fresh jumps.

Logic:

* Polymarket midpoint remains above Kalshi midpoint by a minimum amount for multiple observations or a minimum duration.
* This does not require a fresh Polymarket jump.
* Intended to test whether cross-venue disagreement itself predicts settlement or Kalshi catch-up.

Suggested defaults:

* `min_mid_gap = 0.03`
* `min_duration_seconds = 60`
* `min_observation_count = 3`

Signal type:

* `shadow` initially
* optionally `paper_open` if `fee_adjusted_edge > 0` or if scout mode is enabled

Metadata:

* duration of gap
* number of observations
* max gap
* latest gap
* fee-adjusted edge
* OI/volume deltas

Variant 5: `hold_to_resolution_ev_poly_mid`

Purpose:
Explicitly evaluate the existing thesis:
Polymarket midpoint is a fair-value heuristic, so buy Kalshi if Polymarket midpoint minus Kalshi executable entry minus entry fee is positive.

Logic:

```text
fair_value = Polymarket midpoint
entry_price = Kalshi depth-adjusted buy price
edge = fair_value - entry_price
fee_adjusted_edge = edge * quantity - entry_fee
```

Open paper trade if:

* fee_adjusted_edge > threshold
* Kalshi market has executable buy price
* match is approved
* market not too close to invalid/closed state

Suggested threshold:

* default 0 for scout
* configurable positive amount for stricter mode

This should not require Polymarket to have just moved.

Variant 6: `hold_to_resolution_ev_poly_bid_conservative`

Purpose:
Same as Variant 5, but use a conservative Polymarket reference.

Logic:

```text
fair_value = Polymarket best bid
```

This asks:
If we use the price at which someone is currently willing to buy on Polymarket as fair value, does the strategy still work?

This will fire less often than Polymarket-mid fair value but is more conservative.

Variant 7: `buy_no_mirror`

Purpose:
Test the other side.

Logic:
The current strategy appears mostly focused on buying Kalshi YES when Polymarket YES is higher. Implement mirror logic for NO.

For a matched pair:

* If Polymarket implies the YES probability fell, or NO probability rose, evaluate buying Kalshi NO.
* Use explicit side mapping.
* Do not assume buying NO is identical to selling YES unless the project already represents it that way safely.

Minimum requirement:
Even if full NO orderbook execution is not implemented, record shadow signals for NO-side opportunities.

Signal type:

* `shadow` first
* `paper_open` later once executable Kalshi NO pricing is verified

Important:
Harden side mapping:

* same: Kalshi YES maps to Polymarket YES; Kalshi NO maps to Polymarket NO
* inverse: Kalshi YES maps to Polymarket NO; Kalshi NO maps to Polymarket YES

Add tests for this.

Variant 8: `passive_bid_simulation`

Purpose:
Test maker-style entry without placing live orders.

This is not live maker ordering. It is only simulation.

Logic:
When a fair-value estimate is above Kalshi ask, instead of simulating an immediate taker buy at ask, simulate placing a passive bid below fair value.

Example:

```text
fair value: 0.64
Kalshi bid/ask: 0.58 / 0.61
Taker entry: 0.61
Passive synthetic bid: 0.59 or 0.60
```

Suggested passive price policies:

* `join_best_bid`: bid current Kalshi best bid
* `improve_bid_1c`: bid best bid + 0.01, capped below ask
* `midpoint_bid`: bid midpoint rounded to tick
* `edge_target_bid`: bid fair_value - desired_edge_margin

For first implementation:

* Do not open a normal paper trade immediately.
* Store a `passive_order_simulation` record or `strategy_signal` with signal_type `shadow`.
* Later observations determine whether the passive order would have filled.

Simplified fill assumption:

* If later Kalshi executable sell price is <= synthetic bid price, assume our bid could have been filled.
* If only midpoint touches, do not assume fill.
* If future data is ambiguous, mark fill status as `unknown`.
* Keep assumptions conservative.

Optional table:

```sql
CREATE TABLE IF NOT EXISTS passive_order_sims (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy_signal_id INTEGER NOT NULL,
    run_id TEXT NOT NULL,
    status TEXT NOT NULL, -- open, filled, expired, cancelled, unknown
    opened_at TEXT NOT NULL,
    filled_at TEXT,
    expired_at TEXT,
    kalshi_ticker TEXT NOT NULL,
    polymarket_token_id TEXT NOT NULL,
    side TEXT NOT NULL,
    synthetic_bid_price TEXT NOT NULL,
    fair_value TEXT,
    target_quantity TEXT NOT NULL,
    fill_observation_id INTEGER,
    metadata_json TEXT NOT NULL
);
```

If adding this table is too much for first pass, store passive simulations as `strategy_signals` only and add fill simulation later.

Fair value providers:

Implement lightweight fair value providers so variants can compare different assumptions.

Suggested file:

```text
src/kalshibot/strategies/fair_value.py
```

Interface:

```python
@dataclass(frozen=True)
class FairValueEstimate:
    provider_id: str
    value: Decimal | None
    confidence: Decimal | None
    reasons: tuple[str, ...]
    metadata: dict[str, Any]

class FairValueProvider(Protocol):
    provider_id: str

    def estimate(self, context: StrategyContext) -> FairValueEstimate:
        ...
```

Initial providers:

1. `polymarket_mid`

```text
value = check.polymarket_mid_price
```

2. `polymarket_bid_conservative`

```text
value = Polymarket best bid / conservative executable sell reference if available
```

3. `kalshi_polymarket_mid_blend`

```text
value = 0.5 * polymarket_mid + 0.5 * kalshi_mid
```

4. `liquidity_weighted_blend`
   Use a simple weight based on venue spreads/liquidity if available. Keep it simple and safe.

Example:

```python
poly_quality = 1 / max(poly_spread, Decimal("0.01"))
kalshi_quality = 1 / max(kalshi_spread, Decimal("0.01"))
value = weighted_average(poly_mid, kalshi_mid, poly_quality, kalshi_quality)
```

Do not over-engineer this. The goal is to log different fair-value assumptions and compare them.

Important:
The current `hold_to_resolution_fair_price(check)` returns Polymarket midpoint. Keep that behavior as the default, but allow strategy variants to override fair value provider.

CLI changes:

Add heartbeat options:

```text
--strategy-variants legacy_fee_adjusted_edge,loose_poly_lead_scout,persistent_mid_gap,hold_to_resolution_ev_poly_mid
--strategy-mode scout|strict|off
--strategy-output full|summary|quiet
--shadow-signals / --no-shadow-signals
--strategy-config config/strategy_variants.json
```

Defaults:

* Keep current behavior unless strategy variants are explicitly enabled, OR enable `legacy_fee_adjusted_edge` by default so behavior is unchanged.
* In scout mode, enable permissive variants and shadow signals.
* In strict mode, only variants with positive fee-adjusted edge should open paper trades.
* Off disables new engine.

Example command:

```bash
kalshibot heartbeat \
  --iterations 0 \
  --interval-ms 500 \
  --strategy-mode scout \
  --strategy-variants legacy_fee_adjusted_edge,loose_poly_lead_scout,persistent_mid_gap,hold_to_resolution_ev_poly_mid,hold_to_resolution_ev_poly_bid_conservative
```

Config file example:

```json
{
  "strategy_mode": "scout",
  "variants": {
    "legacy_fee_adjusted_edge": {
      "enabled": true
    },
    "loose_poly_lead_scout": {
      "enabled": true,
      "min_abs_poly_mid_move": "0.01",
      "min_poly_lead_advantage": "0.005",
      "signal_type": "shadow"
    },
    "persistent_mid_gap": {
      "enabled": true,
      "min_mid_gap": "0.03",
      "min_observation_count": 3,
      "min_duration_seconds": 60,
      "signal_type": "shadow"
    },
    "hold_to_resolution_ev_poly_mid": {
      "enabled": true,
      "min_fee_adjusted_edge": "0",
      "signal_type": "paper_open"
    },
    "hold_to_resolution_ev_poly_bid_conservative": {
      "enabled": true,
      "min_fee_adjusted_edge": "0",
      "signal_type": "shadow"
    },
    "passive_bid_simulation": {
      "enabled": false,
      "policy": "improve_bid_1c",
      "desired_edge_margin": "0.03"
    }
  }
}
```

Analysis changes:

Update `kalshibot analyze` so it can report strategy-level results.

Add sections:

1. Strategy signal counts

```text
strategy_id
signal_type
count
markets
avg_score
avg_fee_adjusted_edge
```

2. Strategy paper trades

```text
strategy_id
open_trades
closed_trades
realized_pnl
unrealized_pnl
hold_to_resolution_ev
avg_hold_to_resolution_ev
```

3. Shadow signal follow-through
   For shadow signals, compute what happened after:

* 5 minutes
* 15 minutes
* 60 minutes
  if enough observations exist.

Metrics:

```text
kalshi_mid_change_5m
kalshi_mid_change_15m
kalshi_mid_change_60m
polymarket_mid_change_5m
spread_change_15m
did_kalshi_follow_direction
```

4. By category/profile if available

```text
category
strategy_id
signal_count
paper_trade_count
avg_ev
```

5. Rejection/reason counts

```text
strategy_id
reason
count
```

This is critical because the point is not just opening trades. The point is learning which variants produce useful signals.

Implementation detail:
If follow-through analysis is too much for this PR, at least store shadow signals with enough data to compute it later.

Testing requirements:

Add unit tests for:

1. Strategy registry

* Enabled variants load from config.
* Unknown strategy id fails gracefully or logs warning.
* Legacy strategy can run.

2. Strategy decisions

* `loose_poly_lead_scout` emits shadow when Polymarket moved and Kalshi lagged.
* It does not require OI or volume.
* It handles missing history safely.

3. Hold-to-resolution variants

* `hold_to_resolution_ev_poly_mid` uses Polymarket midpoint as fair value.
* `hold_to_resolution_ev_poly_bid_conservative` uses conservative Polymarket bid/reference.
* Fees are included.
* It can emit paper signal when edge is positive.

4. Multi-strategy paper trades

* Two different strategies can open separate paper trades for the same market.
* Same strategy cannot duplicate an already-open trade for the same market/direction.

5. Side mapping / NO mirror

* Same-side mapping selects correct token for YES/NO.
* Inverse mapping selects correct token for YES/NO.
* Missing sibling token fails safely.

6. Storage migrations

* Existing DBs migrate cleanly.
* New `strategy_signals` table is created.
* Existing observations/paper trades still work.

7. Analysis

* Strategy-level counts work with sample DB rows.

Do not add live trading in this spec.

Non-goals for this iteration:

* No real Kalshi order placement.
* No neural net.
* No strict OI/whale-flow filters.
* No major discovery rewrite.
* No external data source integration yet.
* No Polymarket trading.
* No performance/latency optimization beyond keeping heartbeat functional.

Design principle:
This iteration should make the bot learn more, not trade less.

Current problem:
The basic strategy finds too few opportunities. Therefore, the new engine should record many candidate/shadow signals and allow permissive paper experiments.

Important behavioral distinction:

* Shadow signals are for learning.
* Paper trades are for simulated strategy evaluation.
* Real-money eligibility is a future stricter layer.

Acceptance criteria:

1. Heartbeat can run with multiple strategy variants enabled.
2. Each observation can produce zero, one, or many strategy signals.
3. Strategy signals are persisted with strategy_id, reasons, fair value, edge, and metadata.
4. Existing legacy behavior still works.
5. Paper trade uniqueness includes strategy identity so strategies can be compared on the same market.
6. At least these variants exist:

   * `legacy_fee_adjusted_edge`
   * `loose_poly_lead_scout`
   * `persistent_mid_gap`
   * `hold_to_resolution_ev_poly_mid`
   * `hold_to_resolution_ev_poly_bid_conservative`
7. NO-side/mirror variant can be added as shadow if full executable NO trading is not ready.
8. Analyze command can summarize strategy signal counts and paper trades by strategy.
9. Tests cover strategy evaluation, storage migration, and multi-strategy paper trade uniqueness.
10. README is updated with examples for running heartbeat in scout mode.

Suggested README section to add:

````markdown
## Strategy Variant Engine

Heartbeat can evaluate multiple strategy variants against the same observations. This lets the bot collect shadow signals and paper trades for different hypotheses without changing the core monitor loop.

Example scout run:

```bash
kalshibot heartbeat \
  --iterations 0 \
  --interval-ms 500 \
  --strategy-mode scout \
  --strategy-variants legacy_fee_adjusted_edge,loose_poly_lead_scout,persistent_mid_gap,hold_to_resolution_ev_poly_mid,hold_to_resolution_ev_poly_bid_conservative
````

Scout mode records permissive shadow signals so we can learn whether Polymarket moves, persistent gaps, or hold-to-resolution EV estimates have predictive value. These are not real-money trade recommendations.

Analyze strategy variants:

```bash
kalshibot analyze --db data/observations.sqlite --by-strategy
```

```

Final intent:
After this work, the project should no longer be limited to “Polymarket moved, buy Kalshi.” It should support running a family of strategy hypotheses over the same matched market observations, generating enough paper/shadow data to determine whether any category or strategy variant shows positive expectancy.
```
