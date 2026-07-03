from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

from kalshibot.paper.models import PaperPnl, PaperTradeLogEvent
from kalshibot.paper.pricing import hold_to_resolution_fair_price, paper_hold_to_resolution_ev


def paper_trade_log_event(
    *,
    event: str,
    trade_id: int,
    observation_id: int,
    timed_check: Any,
    purchase_price: Decimal,
    sell_price: Decimal | None,
    pnl: PaperPnl,
    close_reason: str | None,
    fair_value_provider: str | None = None,
    fair_value: Decimal | None = None,
    strategy_id: str | None = None,
    strategy_version: str | None = None,
    strategy_signal_id: int | None = None,
    side: str | None = None,
    direction: str | None = None,
) -> PaperTradeLogEvent:
    check = timed_check.check
    fair_price = (
        fair_value
        if fair_value is not None
        else hold_to_resolution_fair_price(check, fair_value_provider=fair_value_provider)
    )
    hold_ev = paper_hold_to_resolution_ev(
        entry_price=purchase_price,
        fair_price=fair_price,
        quantity=check.target_size,
        entry_fee=pnl.entry_fee,
    )
    return PaperTradeLogEvent(
        event=event,
        trade_id=trade_id,
        observation_id=observation_id,
        run_id=timed_check.run_id,
        timestamp=timed_check.observed_at,
        label=check.label,
        outcome=check.outcome,
        kalshi_ticker=check.kalshi_ticker,
        polymarket_token_id=check.polymarket_token_id,
        purchase_price=purchase_price,
        sell_price=sell_price,
        quantity=check.target_size,
        gross_pnl=pnl.gross,
        entry_fee=pnl.entry_fee,
        exit_fee=pnl.exit_fee,
        fair_price=fair_price,
        fair_value_provider=fair_value_provider,
        hold_to_resolution_ev=hold_ev,
        fee_mode=check.fee_mode,
        fee_adjustment=check.fee_adjustment,
        net_pnl=pnl.net,
        edge=check.polymarket_minus_kalshi,
        close_reason=close_reason,
        kalshi_url=check.kalshi_url,
        polymarket_url=check.polymarket_url,
        strategy_id=strategy_id,
        strategy_version=strategy_version,
        strategy_signal_id=strategy_signal_id,
        side=side,
        direction=direction,
    )


def append_paper_trade_events(path: Path, events: list[PaperTradeLogEvent]) -> None:
    if not events:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as handle:
        for event in events:
            handle.write(json.dumps(format_paper_trade_log_event(event), sort_keys=True) + "\n")


def format_paper_trade_log_event(event: PaperTradeLogEvent) -> dict[str, Any]:
    payload = {
        "event": event.event,
        "trade_id": event.trade_id,
        "observation_id": event.observation_id,
        "run_id": event.run_id,
        "timestamp": event.timestamp,
        "market": event.label,
        "outcome": event.outcome,
        "kalshi_ticker": event.kalshi_ticker,
        "polymarket_token_id": event.polymarket_token_id,
        "kalshi_url": event.kalshi_url,
        "polymarket_url": event.polymarket_url,
        "purchase_price": str(event.purchase_price),
        "sell_price": str(event.sell_price) if event.sell_price is not None else None,
        "quantity": str(event.quantity),
        "gross_pnl": str(event.gross_pnl) if event.gross_pnl is not None else None,
        "entry_fee": str(event.entry_fee),
        "exit_fee": str(event.exit_fee) if event.exit_fee is not None else None,
        "hold_to_resolution_fair_price": str(event.fair_price)
        if event.fair_price is not None
        else None,
        "fair_value_provider": event.fair_value_provider,
        "hold_to_resolution_ev": str(event.hold_to_resolution_ev)
        if event.hold_to_resolution_ev is not None
        else None,
        "fee_mode": event.fee_mode,
        "fee_adjustment": str(event.fee_adjustment),
        "net_pnl": str(event.net_pnl) if event.net_pnl is not None else None,
        "edge": str(event.edge),
        "close_reason": event.close_reason,
    }
    if event.strategy_id is not None:
        payload.update(
            {
                "strategy_id": event.strategy_id,
                "strategy_version": event.strategy_version,
                "strategy_signal_id": event.strategy_signal_id,
                "side": event.side,
                "direction": event.direction,
            }
        )
    return payload
