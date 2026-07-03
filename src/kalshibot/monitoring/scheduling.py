from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass
class CachedPairMetadata:
    open_interest: Decimal | None = None
    volume: Decimal | None = None
    refreshed_at: float | None = None


def metadata_refresh_due(
    cache: CachedPairMetadata | None,
    now: float,
    metadata_refresh_seconds: Decimal,
) -> bool:
    if metadata_refresh_seconds <= 0:
        return True
    if cache is None or cache.refreshed_at is None:
        return True
    return now - cache.refreshed_at >= float(metadata_refresh_seconds)
