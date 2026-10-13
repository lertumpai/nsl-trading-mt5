"""Candidate protocol schemas; live verification status is in docs/protocol.md."""

from decimal import Decimal, InvalidOperation
from typing import Any

import simplejson

from .errors import BrokerError
from .models import Balance, Product, Symbol

LOGIN_URL = "https://auth.iqoption.com/api/v2/login"
LOGOUT_URL = "https://auth.iqoption.com/api/v1.0/logout"
WS_URL = "wss://ws.iqoption.com/echo/websocket"
USER_AGENT = "NSL-IQOption/0.1"


def dumps(value: object) -> str:
    return simplejson.dumps(value, use_decimal=True, allow_nan=False, separators=(",", ":"))


def loads(value: str) -> Any:
    return simplejson.loads(value, use_decimal=True, allow_nan=False)


def decimal(value: object, *, optional: bool = False) -> Decimal | None:
    if value is None and optional:
        return None
    try:
        result = Decimal(str(value))
        if result.is_finite():
            return result
    except (InvalidOperation, ValueError):
        pass
    raise BrokerError("PROTOCOL_UNSUPPORTED", "Broker returned an invalid monetary value.")


def identifier(value: object) -> str:
    if isinstance(value, bool) or not str(value).isdigit() or int(str(value)) <= 0:
        raise BrokerError("PROTOCOL_UNSUPPORTED", "Broker returned an invalid identifier.")
    return str(int(str(value)))


def message(name: str, version: str, body: dict | None = None) -> dict:
    return {"name": name, "version": version, "body": body if body is not None else {}}


def success_acknowledgement(payload: object) -> bool:
    """A delivery acknowledgement contains no requested profile/order data."""
    return payload is True or (
        isinstance(payload, dict)
        and bool(payload)
        and set(payload).issubset({"success", "isSuccessful"})
        and all(value is True for value in payload.values())
    )


def profile_user(payload: object) -> str:
    if isinstance(payload, dict) and isinstance(payload.get("result"), dict):
        payload = payload["result"]
    if not isinstance(payload, dict):
        raise BrokerError("PROTOCOL_UNSUPPORTED", "Broker profile response was not recognized.")
    return identifier(payload.get("user_id", payload.get("id")))


# ISO currency precision for the initial supported fiat balances. Unknown currencies
# remain visible but require explicit broker-provided precision before trading.
CURRENCY_DIGITS = {
    k: 2
    for k in (
        "USD",
        "EUR",
        "GBP",
        "THB",
        "BRL",
        "AUD",
        "CAD",
        "CHF",
        "NZD",
        "ZAR",
        "RUB",
        "INR",
        "CNY",
        "HKD",
        "SGD",
        "MXN",
        "PLN",
        "TRY",
        "IDR",
    )
}
CURRENCY_DIGITS.update({"JPY": 0, "KRW": 0, "VND": 0, "KWD": 3, "BHD": 3})


def parse_balances(payload: object) -> list[Balance]:
    if isinstance(payload, dict):
        payload = payload.get("balances")
    if not isinstance(payload, list):
        raise BrokerError("PROTOCOL_UNSUPPORTED", "Broker balance response was not recognized.")
    balances = []
    try:
        for raw in payload:
            currency = str(raw["currency"]).upper()
            digits = raw.get("currency_digits", CURRENCY_DIGITS.get(currency))
            if digits is not None and (type(digits) is not int or not 0 <= digits <= 8):
                raise ValueError
            balances.append(
                Balance(
                    identifier(raw["id"]),
                    {1: "real", 4: "practice"}.get(int(raw["type"]), "unsupported"),
                    currency,
                    decimal(raw["amount"]),
                    digits,
                )
            )
    except (KeyError, TypeError, ValueError):
        raise BrokerError(
            "PROTOCOL_UNSUPPORTED", "Broker balance fields were not recognized."
        ) from None
    if len({b.id for b in balances}) != len(balances):
        raise BrokerError("PROTOCOL_UNSUPPORTED", "Broker returned duplicate balance identifiers.")
    return balances


def parse_symbols(
    payload: object, payouts: dict[tuple[str, Product], Decimal] | None = None
) -> tuple[Symbol, ...]:
    if not isinstance(payload, dict) or not any(k in payload for k in ("binary", "turbo")):
        raise BrokerError(
            "PROTOCOL_UNSUPPORTED", "Binary initialization response was not recognized."
        )
    symbols = []
    try:
        for product in Product:
            section = payload.get(product.value, {"actives": {}})
            actives = section["actives"]
            if not isinstance(actives, dict):
                raise TypeError
            for key, raw in actives.items():
                active_id = identifier(raw.get("active_id", raw.get("id", key)))
                name = str(raw["name"]).removeprefix("front.")
                symbol = str(raw.get("ticker") or name.replace("/", ""))
                option = raw.get("option", {})
                expirations = option.get("expiration_times", {})
                # Candidate metadata expresses durations in seconds. Never interpret
                # small/ambiguous keys as minutes or substitute a guessed duration.
                values = expirations.keys() if isinstance(expirations, dict) else expirations
                durations = tuple(
                    sorted({int(v) for v in values if int(v) >= 60 and int(v) % 60 == 0})
                )
                schedule = raw.get("schedule")
                if schedule is not None:
                    # The live catalog includes disabled sessions whose end is
                    # before their start. Such intervals have no open instant;
                    # discard them without swapping endpoints or inventing hours.
                    periods = tuple((int(start), int(end)) for start, end in schedule)
                    schedule = tuple((start, end) for start, end in periods if start < end)
                cutoff = int(option.get("deadtime", 30 if product is Product.TURBO else 300))
                if cutoff < 0:
                    raise ValueError
                enabled = raw.get("enabled")
                suspended = raw.get("is_suspended")
                if (enabled is not None and type(enabled) is not bool) or (
                    suspended is not None and type(suspended) is not bool
                ):
                    raise ValueError
                symbols.append(
                    Symbol(
                        active_id,
                        symbol,
                        name,
                        product,
                        symbol.lower().endswith("-otc"),
                        enabled,
                        suspended,
                        schedule,
                        durations,
                        cutoff,
                        (payouts or {}).get((active_id, product)),
                        decimal(raw.get("minimal_bet"), optional=True),
                        decimal(raw.get("maximal_bet"), optional=True),
                    )
                )
    except (KeyError, ValueError, TypeError):
        raise BrokerError(
            "PROTOCOL_UNSUPPORTED",
            "Binary instrument fields were not recognized; trading is disabled.",
        ) from None
    if len({(s.active_id, s.product) for s in symbols}) != len(symbols):
        raise BrokerError(
            "PROTOCOL_UNSUPPORTED", "Duplicate product/active IDs in instrument response."
        )
    return tuple(symbols)


def expiration_for(symbol: Symbol, minutes: int, now: float) -> int:
    if type(minutes) is not int or minutes <= 0 or minutes * 60 not in symbol.expiration_seconds:
        raise BrokerError("INVALID_EXPIRATION", "Select an expiration listed for this product.")
    if symbol.product is Product.TURBO:
        if not 1 <= minutes <= 5:
            raise BrokerError(
                "INVALID_EXPIRATION", "This adapter supports turbo durations of 1–5 minutes."
            )
        base = (int(now) // 60 + 1) * 60
        if base - now <= symbol.purchase_cutoff:
            base += 60
        expiration = base + (minutes - 1) * 60
    else:
        if minutes % 15:
            raise BrokerError(
                "INVALID_EXPIRATION", "This adapter supports binary durations in 15-minute steps."
            )
        expiration = (int(now) // 900) * 900 + minutes * 60
    validate_expiration(symbol, expiration, now)
    return expiration


def validate_expiration(symbol: Symbol, expiration: int, now: float) -> None:
    alignment = 60 if symbol.product is Product.TURBO else 900
    if expiration % alignment or expiration - now <= symbol.purchase_cutoff:
        raise BrokerError(
            "INVALID_EXPIRATION", "The selected purchase window has closed; prepare a new order."
        )
    if symbol.schedule is None or not any(
        start <= now < expiration <= end for start, end in symbol.schedule
    ):
        raise BrokerError(
            "INVALID_EXPIRATION", "Expiration must fall within an open broker schedule."
        )


def order_body(order: object) -> dict:
    return message(
        "binary-options.open-option",
        "1.0",
        {
            "user_balance_id": int(order.balance_id),
            "active_id": int(order.active_id),
            "option_type_id": order.product.option_type_id,
            "direction": order.direction,
            "expired": order.expiration,
            "price": order.amount,
        },
    )
