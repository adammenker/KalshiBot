from __future__ import annotations

from decimal import Decimal, ROUND_CEILING

KALSHI_TAKER_FEE_RATE = Decimal("0.07")
CENT = Decimal("0.01")


def kalshi_taker_fee(price: Decimal, contracts: Decimal) -> Decimal:
    if contracts <= 0:
        raise ValueError("contracts must be positive")
    if price < 0 or price > 1:
        raise ValueError("price must be between 0 and 1")
    raw_fee = KALSHI_TAKER_FEE_RATE * contracts * price * (Decimal("1") - price)
    return round_up_to_cent(raw_fee)


def kalshi_round_trip_fee(
    *,
    entry_price: Decimal,
    exit_price: Decimal,
    contracts: Decimal,
) -> Decimal:
    return kalshi_taker_fee(entry_price, contracts) + kalshi_taker_fee(exit_price, contracts)


def round_up_to_cent(amount: Decimal) -> Decimal:
    if amount <= 0:
        return Decimal("0")
    return (amount / CENT).to_integral_value(rounding=ROUND_CEILING) * CENT
