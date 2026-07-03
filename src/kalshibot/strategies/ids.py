from __future__ import annotations

from collections.abc import Iterable

LEGACY_FEE_ADJUSTED_EDGE_ID = "legacy_fee_adjusted_edge"
LOOSE_POLY_LEAD_SCOUT_ID = "loose_poly_lead_scout"
PERSISTENT_MID_GAP_ID = "persistent_mid_gap"
HOLD_TO_RESOLUTION_EV_POLY_MID_ID = "hold_to_resolution_ev_poly_mid"
HOLD_TO_RESOLUTION_EV_POLY_BID_CONSERVATIVE_ID = "hold_to_resolution_ev_poly_bid_conservative"

SCOUT_STRATEGY_IDS = (
    LEGACY_FEE_ADJUSTED_EDGE_ID,
    LOOSE_POLY_LEAD_SCOUT_ID,
    PERSISTENT_MID_GAP_ID,
    HOLD_TO_RESOLUTION_EV_POLY_MID_ID,
    HOLD_TO_RESOLUTION_EV_POLY_BID_CONSERVATIVE_ID,
)
STRICT_STRATEGY_IDS = (
    LEGACY_FEE_ADJUSTED_EDGE_ID,
    HOLD_TO_RESOLUTION_EV_POLY_MID_ID,
    HOLD_TO_RESOLUTION_EV_POLY_BID_CONSERVATIVE_ID,
)
BUILT_IN_STRATEGY_IDS = SCOUT_STRATEGY_IDS


def parse_enabled_strategy_ids(value: str | Iterable[str] | None) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        raw_ids = value.split(",")
    else:
        raw_ids = value
    normalized = [strategy_id.strip() for strategy_id in raw_ids if strategy_id.strip()]
    return tuple(dict.fromkeys(normalized))
