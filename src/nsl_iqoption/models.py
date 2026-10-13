import time
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum

from .errors import BrokerError


class SessionState(StrEnum):
    DISCONNECTED = "DISCONNECTED"
    AUTHENTICATING = "AUTHENTICATING"
    CHALLENGE_REQUIRED = "CHALLENGE_REQUIRED"
    CONNECTED = "CONNECTED"
    RECONNECTING = "RECONNECTING"
    SESSION_EXPIRED = "SESSION_EXPIRED"
    ERROR = "ERROR"


class Product(StrEnum):
    BINARY = "binary"
    TURBO = "turbo"

    @property
    def instrument_type(self) -> str:
        return f"{self.value}-option"

    @property
    def option_type_id(self) -> int:
        return 1 if self is Product.BINARY else 3


class OrderState(StrEnum):
    PREPARED = "PREPARED"
    SUBMITTING = "SUBMITTING"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    UNKNOWN = "UNKNOWN"
    SETTLED = "SETTLED"


@dataclass(frozen=True)
class Balance:
    id: str
    mode: str
    currency: str
    amount: Decimal
    currency_digits: int | None


@dataclass(frozen=True)
class Session:
    user_id: str
    balance_id: str | None
    state: SessionState


@dataclass(frozen=True)
class AuthChallenge:
    challenge_id: str
    kind: str
    message: str
    supported: bool = False


@dataclass(frozen=True)
class Symbol:
    active_id: str
    symbol: str
    display_name: str
    product: Product
    otc: bool
    enabled: bool | None
    suspended: bool | None
    schedule: tuple[tuple[int, int], ...] | None
    expiration_seconds: tuple[int, ...]
    purchase_cutoff: int
    payout_percent: Decimal | None
    min_stake: Decimal | None
    max_stake: Decimal | None

    def market_status(self, now: float | None) -> str:
        if self.enabled is False or self.suspended is True:
            return "closed"
        if now is None or self.enabled is None or self.suspended is None or self.schedule is None:
            return "unknown"
        return "open" if any(start <= now < end for start, end in self.schedule) else "closed"


@dataclass(frozen=True)
class SymbolCatalog:
    symbols: tuple[Symbol, ...]
    refreshed_at: float
    stale: bool


@dataclass(frozen=True)
class OrderInput:
    symbol: str
    product: Product
    direction: str
    amount: Decimal
    duration_minutes: int


@dataclass(frozen=True)
class Order:
    local_id: str
    user_id: str
    balance_id: str
    mode: str
    currency: str
    active_id: str
    symbol: str
    product: Product
    direction: str
    amount: Decimal
    duration_minutes: int
    expiration: int
    created_at: float
    state: OrderState = OrderState.PREPARED
    request_id: str | None = None
    broker_id: str | None = None
    sent_at: float | None = None
    gross_return: Decimal | None = None
    net_profit: Decimal | None = None
    error_code: str | None = None


@dataclass
class ServerClock:
    stale_after: float = 10.0
    monotonic: Callable[[], float] = field(default=time.monotonic, repr=False)
    _timestamp: float | None = field(default=None, repr=False)
    _sample_at: float = field(default=0.0, repr=False)

    def update(self, timestamp_ms: object) -> None:
        try:
            value = float(timestamp_ms) / 1000
            if not 1_000_000_000 <= value < 10_000_000_000:
                return
        except (ValueError, TypeError, OverflowError):
            return
        self._timestamp = value
        self._sample_at = self.monotonic()

    def reset(self) -> None:
        self._timestamp = None

    def now(self) -> float:
        age = self.monotonic() - self._sample_at
        if self._timestamp is None or age > self.stale_after:
            raise BrokerError("METADATA_STALE", "Fresh broker time is required before trading.")
        return self._timestamp + age
