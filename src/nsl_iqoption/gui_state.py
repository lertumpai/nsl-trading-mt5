"""Immutable snapshots and ticket validation shared by the desktop views."""

import time
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from .errors import BrokerError
from .models import Balance, Order, OrderInput, OrderState, Product, SessionState, Symbol


@dataclass(frozen=True)
class DesktopSnapshot:
    state: SessionState = SessionState.DISCONNECTED
    user_id: str | None = None
    selected_balance_id: str | None = None
    balances: tuple[Balance, ...] = ()
    symbols: tuple[Symbol, ...] = ()
    orders: tuple[Order, ...] = ()
    catalog_at: float | None = None
    catalog_stale: bool = True
    server_time: float | None = None
    sampled_at: float = 0.0

    def now(self) -> float | None:
        age = time.monotonic() - self.sampled_at
        if self.state is not SessionState.CONNECTED or self.server_time is None or age > 3:
            return None
        return self.server_time + age

    def selected_balance(self) -> Balance | None:
        return next((b for b in self.balances if b.id == self.selected_balance_id), None)


def supported_minutes(symbol: Symbol) -> tuple[int, ...]:
    return tuple(
        seconds // 60
        for seconds in symbol.expiration_seconds
        if seconds % 60 == 0
        and (
            (symbol.product is Product.TURBO and 60 <= seconds <= 300)
            or (symbol.product is Product.BINARY and seconds > 0 and seconds % 900 == 0)
        )
    )


def expiration_choices(
    symbol: Symbol, symbols: tuple[Symbol, ...]
) -> tuple[tuple[int, Symbol], ...]:
    """Present all advertised expiries for the selected asset, preserving product routing."""
    choices = {}
    for candidate in symbols:
        if candidate.active_id != symbol.active_id or candidate.symbol != symbol.symbol:
            continue
        for minutes in supported_minutes(candidate):
            choices[minutes] = candidate
    return tuple(sorted(choices.items()))


def ticket_input(symbol: Symbol, amount: str, minutes: int, direction: str) -> OrderInput:
    try:
        stake = Decimal(amount.strip())
    except InvalidOperation:
        raise BrokerError("INVALID_AMOUNT", "Enter a decimal stake amount.") from None
    if not stake.is_finite() or stake <= 0:
        raise BrokerError("INVALID_AMOUNT", "Enter a positive stake amount.")
    if minutes not in supported_minutes(symbol):
        raise BrokerError("INVALID_EXPIRATION", "Select an available duration for this product.")
    if direction not in ("call", "put"):
        raise BrokerError("INVALID_DIRECTION", "Select CALL or PUT.")
    return OrderInput(symbol.active_id, symbol.product, direction, stake, minutes)


def review_status(order: Order, snapshot: DesktopSnapshot, cutoff: int) -> tuple[bool, str]:
    if order.state is not OrderState.PREPARED:
        return False, "This order has already been submitted or resolved."
    if snapshot.user_id != order.user_id or snapshot.state is not SessionState.CONNECTED:
        return False, "An authenticated connection is required to submit."
    balance = next((b for b in snapshot.balances if b.id == order.balance_id), None)
    if balance is None or balance.mode != order.mode or balance.currency != order.currency:
        return False, "The reviewed account is no longer available. Prepare a new order."
    now = snapshot.now()
    if now is None:
        return False, "Waiting for fresh broker time."
    if order.expiration - now <= cutoff:
        return False, "Purchase window closed. Close this review and prepare a new order."
    return True, f"Expires in {int(order.expiration - now)} seconds."
