from __future__ import annotations

from decimal import Decimal
from typing import Any

from kalshibot.fees import kalshi_taker_fee
from kalshibot.paper.models import PaperPnl, PaperTradeSnapshot
from kalshibot.spreads import SpreadCheck
from kalshibot.utils import optional_decimal


def paper_trade_pnl(
    *,
    entry_price: Decimal,
    mark_price: Decimal | None,
    quantity: Decimal,
    entry_fee: Decimal | None = None,
    exit_fee: Decimal | None = None,
) -> PaperPnl:
    resolved_entry_fee = entry_fee if entry_fee is not None else kalshi_taker_fee(entry_price, quantity)
    if mark_price is None:
        return PaperPnl(gross=None, entry_fee=resolved_entry_fee, exit_fee=None, net=None)
    resolved_exit_fee = exit_fee if exit_fee is not None else kalshi_taker_fee(mark_price, quantity)
    gross = (mark_price - entry_price) * quantity
    return PaperPnl(
        gross=gross,
        entry_fee=resolved_entry_fee,
        exit_fee=resolved_exit_fee,
        net=gross - resolved_entry_fee - resolved_exit_fee,
    )


def paper_trade_snapshot(
    check: SpreadCheck,
    *,
    entry_price: Decimal,
    quantity: Decimal | None = None,
    entry_fee: Decimal | None = None,
    fair_value_provider: str | None = None,
    fair_value: Decimal | None = None,
) -> PaperTradeSnapshot:
    resolved_quantity = quantity or check.target_size
    pnl = paper_trade_pnl(
        entry_price=entry_price,
        mark_price=check.kalshi_sell_price,
        quantity=resolved_quantity,
        entry_fee=entry_fee if entry_fee is not None else check.kalshi_entry_fee,
        exit_fee=check.kalshi_exit_fee,
    )
    fair_price = (
        fair_value
        if fair_value is not None
        else hold_to_resolution_fair_price(check, fair_value_provider=fair_value_provider)
    )
    return PaperTradeSnapshot(
        entry_price=entry_price,
        quantity=resolved_quantity,
        mark_price=check.kalshi_sell_price,
        fair_price=fair_price,
        pnl=pnl,
        hold_to_resolution_ev=paper_hold_to_resolution_ev(
            entry_price=entry_price,
            fair_price=fair_price,
            quantity=resolved_quantity,
            entry_fee=pnl.entry_fee,
        ),
    )


def hold_to_resolution_fair_price(
    check: SpreadCheck,
    *,
    fair_value_provider: str | None = None,
) -> Decimal | None:
    if fair_value_provider == "polymarket_bid_conservative":
        return check.polymarket_sell_price
    return check.polymarket_mid_price


def paper_hold_to_resolution_ev(
    *,
    entry_price: Decimal,
    fair_price: Decimal | None,
    quantity: Decimal,
    entry_fee: Decimal,
) -> Decimal | None:
    if fair_price is None:
        return None
    return (fair_price - entry_price) * quantity - entry_fee


def trade_entry_fee(trade_row: dict[str, Any]) -> Decimal:
    existing = optional_decimal(trade_row.get("entry_fee"))
    if existing is not None:
        return existing
    return kalshi_taker_fee(
        Decimal(str(trade_row["entry_price"])),
        Decimal(str(trade_row["quantity"])),
    )


def optional_decimal_string(value: Decimal | None) -> str | None:
    return str(value) if value is not None else None
