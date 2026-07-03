from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class PaperExitConfig:
    exit_edge: Decimal | None = None
    take_profit: Decimal | None = None
    stop_loss: Decimal | None = None
    max_hold_minutes: int | None = None


@dataclass(frozen=True)
class PaperPnl:
    gross: Decimal | None
    entry_fee: Decimal
    exit_fee: Decimal | None
    net: Decimal | None


@dataclass(frozen=True)
class PaperTradeSnapshot:
    entry_price: Decimal
    quantity: Decimal
    mark_price: Decimal | None
    fair_price: Decimal | None
    pnl: PaperPnl
    hold_to_resolution_ev: Decimal | None


@dataclass(frozen=True)
class PaperTradeLogEvent:
    event: str
    trade_id: int
    observation_id: int
    run_id: str
    timestamp: str
    label: str
    outcome: str
    kalshi_ticker: str
    polymarket_token_id: str
    purchase_price: Decimal
    sell_price: Decimal | None
    quantity: Decimal
    gross_pnl: Decimal | None
    entry_fee: Decimal
    exit_fee: Decimal | None
    fair_price: Decimal | None
    fair_value_provider: str | None
    hold_to_resolution_ev: Decimal | None
    fee_mode: str
    fee_adjustment: Decimal
    net_pnl: Decimal | None
    edge: Decimal
    close_reason: str | None = None
    kalshi_url: str = ""
    polymarket_url: str = ""
    strategy_id: str | None = None
    strategy_version: str | None = None
    strategy_signal_id: int | None = None
    side: str | None = None
    direction: str | None = None
