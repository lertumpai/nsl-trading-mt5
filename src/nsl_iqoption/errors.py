"""Errors contain deliberately bounded, secret-free descriptions."""

import re


class BrokerError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


class RequestError(BrokerError):
    def __init__(self, code: str, message: str, *, may_have_sent: bool):
        self.may_have_sent = may_have_sent
        super().__init__(code, message)


class BrokerRejection(BrokerError):
    """An explicit broker response proving that its request was rejected."""


def broker_failure(payload: object) -> BrokerError | None:
    """Inspect error indicators without reflecting arbitrary server text or tokens."""
    if payload is False:
        return BrokerRejection("BROKER_REJECTED", "Broker rejected the request.")
    if not isinstance(payload, dict):
        return None
    failed = (
        payload.get("success") is False
        or payload.get("isSuccessful") is False
        or payload.get("status") in ("error", "failed", "rejected")
        or payload.get("error") not in (None, False, "", 0)
    )
    if not failed:
        return None
    text = str(payload.get("message", payload.get("error", ""))).lower()
    if any(s in text for s in ("version", "unknown method", "unknown message", "not supported")):
        return BrokerRejection(
            "PROTOCOL_UNSUPPORTED", "Broker does not support this protocol request."
        )
    if any(
        s in text for s in ("unauthorized", "not authenticated", "session expired", "invalid ssid")
    ):
        return BrokerRejection("SESSION_EXPIRED", "Broker session expired; log in again.")
    if "balance" in text and any(s in text for s in ("insufficient", "not enough")):
        return BrokerRejection(
            "INSUFFICIENT_BALANCE", "Broker reports insufficient available funds."
        )
    if any(s in text for s in ("market closed", "asset is closed", "suspended")):
        return BrokerRejection(
            "SYMBOL_UNAVAILABLE", "Broker reports that this market is unavailable."
        )
    if "expir" in text:
        return BrokerRejection("INVALID_EXPIRATION", "Broker rejected the expiration.")
    code = str(payload.get("code", ""))
    suffix = f" (code {code})" if re.fullmatch(r"[0-9]{1,6}", code) else ""
    return BrokerRejection("BROKER_REJECTED", f"Broker rejected the request{suffix}.")
