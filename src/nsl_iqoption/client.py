"""Public broker service. Orders are never retried by reconnection logic."""

import asyncio
import logging
import random
import time
import uuid
from dataclasses import replace
from decimal import Decimal, InvalidOperation
from pathlib import Path

from .auth import AuthClient
from .errors import BrokerError, BrokerRejection, RequestError, broker_failure
from .journal import OrderJournal
from .models import (
    CHART_TIMEFRAMES,
    AuthChallenge,
    Balance,
    CandleSeries,
    Order,
    OrderInput,
    OrderState,
    Product,
    Session,
    SessionState,
    Symbol,
    SymbolCatalog,
)
from .protocol import (
    decimal,
    expiration_for,
    identifier,
    message,
    order_body,
    parse_balances,
    parse_candles,
    parse_symbols,
    payout_from_commission,
    profile_user,
    validate_expiration,
)
from .transport import WebSocketTransport

log = logging.getLogger(__name__)


class IQOptionClient:
    def __init__(
        self,
        journal_path: str | Path = ".runtime/orders.sqlite3",
        *,
        auth: AuthClient | None = None,
        transport: WebSocketTransport | None = None,
    ):
        self.auth = auth or AuthClient()
        self.transport = transport or WebSocketTransport()
        self.journal = OrderJournal(journal_path)
        self.state = SessionState.DISCONNECTED
        self.user_id: str | None = None
        self.selected_balance_id: str | None = None
        self.balances: list[Balance] = []
        self._real_enabled: set[str] = set()
        self._symbols: tuple[Symbol, ...] | None = None
        self._catalog_at = 0.0
        self._catalog_mono = 0.0
        self._catalog_lock = asyncio.Lock()
        self._trade_lock = asyncio.Lock()
        self._closing = True
        self._reconnect_task: asyncio.Task | None = None
        self._refresh_task: asyncio.Task | None = None
        self._payout_task: asyncio.Task | None = None
        self._catalog_generation = 0
        self._balance_dirty = False
        self.protocol_versions: dict[str, str] = {}
        self.transport.on_event = self._on_event
        self.transport.on_disconnect = self._on_disconnect

    async def login(self, identifier_value: str, secret: str) -> Session | AuthChallenge:
        await self.logout()
        self.state = SessionState.AUTHENTICATING
        try:
            result = await self.auth.login(identifier_value, secret)
            if isinstance(result, AuthChallenge):
                self.state = SessionState.CHALLENGE_REQUIRED
                await self.auth.close()
                return result
            self._closing = False
            await self.transport.connect(self.auth.session, self.auth.ssid)
            await self._load_account()
            if not self.transport.connected:
                raise BrokerError("NETWORK_TIMEOUT", "Broker disconnected before login completed.")
            self.state = SessionState.CONNECTED
            practices = [b for b in self.balances if b.mode == "practice"]
            self.selected_balance_id = practices[0].id if practices else None
            self._refresh_task = asyncio.create_task(self._refresh_loop(), name="iqoption-refresh")
            return Session(self.user_id, self.selected_balance_id, self.state)
        except BaseException as error:
            await self.logout()
            self.state = (
                SessionState.SESSION_EXPIRED
                if isinstance(error, BrokerError) and error.code == "SESSION_EXPIRED"
                else SessionState.ERROR
            )
            raise

    async def complete_challenge(self, challenge_id: str, code: str) -> Session:
        await self.auth.complete_challenge(challenge_id, code)
        raise BrokerError("PROTOCOL_UNSUPPORTED", "Challenge completion is not implemented.")

    async def _load_account(self) -> None:
        raw = await self.transport.request(
            message("core.get-profile", "1.0"), expected={"profile", "result"}
        )
        user_id = profile_user(raw)
        if self.user_id is not None and self.user_id != user_id:
            raise BrokerError("ACCOUNT_UNAVAILABLE", "Reconnection returned a different account.")
        self.user_id = user_id
        await self._load_balances()
        await self.transport.wait_for_time()
        # Catalog incompatibility does not change whether login itself succeeded.
        await self._subscribe()

    async def _load_balances(self) -> list[Balance]:
        raw = await self.transport.request(
            message(
                "get-balances", "1.0", {"types_ids": [1, 4, 2], "tournaments_statuses_ids": [3, 2]}
            ),
            expected={"balances", "result"},
        )
        self.balances = parse_balances(raw)
        self._balance_dirty = False
        if self.selected_balance_id and not any(
            b.id == self.selected_balance_id for b in self.balances
        ):
            self.selected_balance_id = None
        return self.balances

    async def _subscribe(self) -> None:
        subscriptions = [("internal-billing.balance-changed", "1.0", {})]
        for product in Product:
            subscriptions.append(
                (
                    "portfolio.order-changed",
                    "2.0",
                    {"user_id": int(self.user_id), "instrument_type": product.value},
                )
            )
            for balance in self.balances:
                if balance.mode in ("practice", "real"):
                    subscriptions.append(
                        (
                            "portfolio.position-changed",
                            "3.0",
                            {
                                "user_id": int(self.user_id),
                                "user_balance_id": int(balance.id),
                                "instrument_type": product.instrument_type,
                            },
                        )
                    )
        for name, version, filters in subscriptions:
            try:
                await self.transport.subscribe(name, version, filters)
            except BrokerError as error:
                if error.code in ("SESSION_EXPIRED", "NETWORK_TIMEOUT"):
                    raise
                log.warning(
                    "Subscription unavailable: operation=%s code=%s; history polling remains available",
                    name,
                    error.code,
                )

    def _require_connection(self) -> None:
        if self.state is not SessionState.CONNECTED or not self.transport.connected:
            raise BrokerError(
                "SESSION_EXPIRED", f"Session is {self.state}; wait for reconnect or log in again."
            )

    async def list_balances(self) -> list[Balance]:
        self._require_connection()
        return await self._load_balances()

    async def get_candles(
        self, active_id: str, timeframe: int = 60, *, count: int = 120
    ) -> CandleSeries:
        """Read price history for an account-listed active, independently of trade expiry."""
        self._require_connection()
        if type(timeframe) is not int or timeframe not in {v for _, v in CHART_TIMEFRAMES}:
            raise BrokerError("INVALID_INPUT", "Choose a supported chart timeframe.")
        if type(count) is not int or not 1 <= count <= 500:
            raise BrokerError("INVALID_INPUT", "Request between 1 and 500 candles.")
        if self._symbols is None:
            await self.list_symbols()
        active_id = identifier(active_id)
        if not any(symbol.active_id == active_id for symbol in self._symbols):
            raise BrokerError("SYMBOL_UNAVAILABLE", "Select a symbol from the broker catalog.")
        now = self.transport.clock.now()
        raw = await self.transport.request(
            message(
                "get-candles",
                "2.0",
                {
                    "active_id": int(active_id),
                    "size": timeframe,
                    "to": int(now),
                    "count": count,
                    "split_normalization": True,
                },
            ),
            expected={"candles", "result"},
        )
        candles = parse_candles(raw, timeframe)
        if any(c.start > now or c.end > now + timeframe for c in candles):
            raise BrokerError("PROTOCOL_UNSUPPORTED", "Broker returned future candle times.")
        return CandleSeries(active_id, timeframe, candles[-count:], now)

    def select_balance(self, balance_id: str, *, enable_real: bool = False) -> Balance:
        self._require_connection()
        balance = self._balance(balance_id)
        if balance.mode == "unsupported":
            raise BrokerError(
                "ACCOUNT_UNAVAILABLE", "This balance type is not supported for trading."
            )
        if balance.mode == "real" and not enable_real:
            raise BrokerError(
                "ACCOUNT_UNAVAILABLE", "Explicitly enable real mode when selecting this balance."
            )
        if balance.mode == "real":
            self._real_enabled.add(balance.id)
        self.selected_balance_id = balance.id
        return balance

    def _balance(self, balance_id: str | None) -> Balance:
        for balance in self.balances:
            if balance.id == balance_id:
                return balance
        raise BrokerError(
            "ACCOUNT_UNAVAILABLE",
            "Select an available balance. No real balance is selected automatically.",
        )

    async def _payouts(self) -> dict[tuple[str, Product], Decimal]:
        payouts = {}
        for product in Product:
            try:
                async with asyncio.timeout(2):
                    payload = await self.transport.request(
                        message(
                            "trading-settings.get-trading-group-params",
                            "2.0",
                            {"instrument_type": product.instrument_type},
                        ),
                        expected={"trading-group-params", "result"},
                    )
                if not isinstance(payload, dict):
                    continue
                for item in payload.get("commissions", []):
                    payout = payout_from_commission(item["value"])
                    if payout is not None:
                        payouts[(identifier(item["active_id"]), product)] = payout
            except TimeoutError:
                log.info("Optional payout lookup timed out: product=%s", product)
            except BrokerError as error:
                if error.code == "SESSION_EXPIRED":
                    raise
                log.info("Payout metadata unavailable: product=%s code=%s", product, error.code)
            except (KeyError, TypeError):
                log.info("Payout metadata schema unrecognized: product=%s", product)
        return payouts

    async def _refresh_payouts(self, generation: int) -> None:
        try:
            payouts = await self._payouts()
            if generation != self._catalog_generation or self.state is not SessionState.CONNECTED:
                return
            self._symbols = tuple(
                replace(symbol, payout_percent=payouts.get((symbol.active_id, symbol.product)))
                if symbol.payout_percent is None
                else symbol
                for symbol in self._symbols
            )
        except BrokerError as error:
            log.info("Optional payout refresh unavailable: code=%s", error.code)

    async def _refresh_catalog(self) -> None:
        async with self._catalog_lock:
            payload = None
            versions = (
                (self.protocol_versions["initialization"],)
                if "initialization" in self.protocol_versions
                else ("4.0", "3.0")
            )
            for version in versions:
                try:
                    payload = await self.transport.request(
                        message("get-initialization-data", version),
                        expected={"initialization-data", "result"},
                    )
                    # Validate schema before recording a working version.
                    parse_symbols(payload)
                    self.protocol_versions["initialization"] = version
                    break
                except BrokerError as error:
                    if error.code != "PROTOCOL_UNSUPPORTED" or version == versions[-1]:
                        raise
            # Publish eligibility immediately. Optional payout transport must not
            # consume the five-second preparation/submission freshness window.
            self._catalog_at = time.time()
            self._catalog_mono = time.monotonic()
            self._symbols = parse_symbols(payload)
            self._catalog_generation += 1
            if self._payout_task is not None:
                self._payout_task.cancel()
            self._payout_task = None
            if any(symbol.payout_percent is None for symbol in self._symbols):
                self._payout_task = asyncio.create_task(
                    self._refresh_payouts(self._catalog_generation), name="iqoption-payouts"
                )

    async def list_symbols(
        self,
        *,
        search: str = "",
        open_only: bool = False,
        otc: bool | None = None,
        refresh: bool = False,
    ) -> SymbolCatalog:
        self._require_connection()
        if refresh or self._symbols is None or time.monotonic() - self._catalog_mono > 30:
            await self._refresh_catalog()
        try:
            now = self.transport.clock.now()
        except BrokerError:
            now = None
        symbols = tuple(
            s
            for s in self._symbols
            if (
                not search or search.lower() in f"{s.symbol} {s.display_name} {s.active_id}".lower()
            )
            and (not open_only or s.market_status(now) == "open")
            and (otc is None or s.otc is otc)
        )
        return SymbolCatalog(
            symbols, self._catalog_at, now is None or time.monotonic() - self._catalog_mono > 30
        )

    def cached_symbols(self) -> SymbolCatalog | None:
        """A read-only view for interfaces; never starts a broker request."""
        if self._symbols is None:
            return None
        try:
            self.transport.clock.now()
            stale = self.state is not SessionState.CONNECTED
        except BrokerError:
            stale = True
        return SymbolCatalog(
            self._symbols, self._catalog_at, stale or time.monotonic() - self._catalog_mono > 30
        )

    async def _fresh_symbol(self, active_or_symbol: str, product: Product) -> Symbol:
        if self._symbols is None or time.monotonic() - self._catalog_mono > 5:
            await self._refresh_catalog()
        if time.monotonic() - self._catalog_mono > 5:
            raise BrokerError(
                "METADATA_STALE",
                "Instrument eligibility became stale during refresh; retry when the broker responds promptly.",
            )
        matches = [
            s
            for s in self._symbols
            if s.product is product
            and (
                s.active_id == active_or_symbol
                or s.symbol.casefold() == active_or_symbol.casefold()
            )
        ]
        if len(matches) != 1:
            raise BrokerError(
                "SYMBOL_UNAVAILABLE",
                "Select one exact symbol/active ID and product from the broker catalog.",
            )
        symbol = matches[0]
        if symbol.market_status(self.transport.clock.now()) != "open":
            raise BrokerError(
                "SYMBOL_UNAVAILABLE", "This product is closed or its eligibility is unknown."
            )
        return symbol

    def _validate_stake(self, amount: Decimal, balance: Balance, symbol: Symbol) -> None:
        if balance.mode not in ("practice", "real") or (
            balance.mode == "real" and balance.id not in self._real_enabled
        ):
            raise BrokerError(
                "ACCOUNT_UNAVAILABLE",
                "Real trading requires explicit balance selection in this session.",
            )
        if not isinstance(amount, Decimal) or not amount.is_finite() or amount <= 0:
            raise BrokerError("INVALID_AMOUNT", "Stake must be a positive finite decimal amount.")
        if balance.currency_digits is None:
            raise BrokerError("INVALID_AMOUNT", "Currency precision is unknown for this balance.")
        try:
            if amount != amount.quantize(Decimal(1).scaleb(-balance.currency_digits)):
                raise BrokerError(
                    "INVALID_AMOUNT",
                    f"Use at most {balance.currency_digits} decimal places for {balance.currency}.",
                )
        except InvalidOperation:
            raise BrokerError(
                "INVALID_AMOUNT", "Stake exceeds the supported decimal range."
            ) from None
        if (symbol.min_stake is not None and amount < symbol.min_stake) or (
            symbol.max_stake is not None and amount > symbol.max_stake
        ):
            raise BrokerError("INVALID_AMOUNT", "Stake is outside the broker's product limits.")
        if amount > balance.amount:
            raise BrokerError("INSUFFICIENT_BALANCE", "Stake exceeds the current balance.")

    def _check_unknown(self, balance_id: str, *, acknowledged: bool) -> None:
        unknown = [
            o
            for o in self.journal.list(self.user_id)
            if o.balance_id == balance_id and o.state in (OrderState.UNKNOWN, OrderState.SUBMITTING)
        ]
        if unknown and not acknowledged:
            raise BrokerError(
                "ORDER_STATUS_UNKNOWN",
                "Unresolved orders exist on this balance. Review/reconcile orders, then explicitly acknowledge them before preparing another.",
            )

    async def prepare_order(
        self, order_input: OrderInput, *, acknowledge_unknown: bool = False
    ) -> Order:
        self._require_connection()
        async with self._trade_lock:
            await self._load_balances()
            balance = self._balance(self.selected_balance_id)
            self._check_unknown(balance.id, acknowledged=acknowledge_unknown)
            symbol = await self._fresh_symbol(order_input.symbol, Product(order_input.product))
            direction = order_input.direction.lower()
            if direction not in ("call", "put"):
                raise BrokerError("INVALID_DIRECTION", "Direction must be CALL or PUT.")
            self._validate_stake(order_input.amount, balance, symbol)
            expiration = expiration_for(
                symbol, order_input.duration_minutes, self.transport.clock.now()
            )
            order = Order(
                str(uuid.uuid4()),
                self.user_id,
                balance.id,
                balance.mode,
                balance.currency,
                symbol.active_id,
                symbol.symbol,
                symbol.product,
                direction,
                order_input.amount,
                order_input.duration_minutes,
                expiration,
                time.time(),
            )
            return self.journal.save(order)

    async def submit_order(
        self, local_order_id: str, *, acknowledge_unknown: bool = False
    ) -> Order:
        self._require_connection()
        async with self._trade_lock:
            order = self.journal.get(local_order_id)
            if order.user_id != self.user_id:
                raise BrokerError("ACCOUNT_UNAVAILABLE", "This intent belongs to another account.")
            if order.state is not OrderState.PREPARED:
                raise BrokerError(
                    "ORDER_ALREADY_SUBMITTED",
                    "This intent has already been submitted or resolved; it cannot be sent again.",
                )
            await self._load_balances()
            balance = self._balance(order.balance_id)
            if balance.mode != order.mode or balance.currency != order.currency:
                raise BrokerError(
                    "ACCOUNT_UNAVAILABLE", "The prepared balance has changed; prepare a new intent."
                )
            self._check_unknown(balance.id, acknowledged=acknowledge_unknown)
            symbol = await self._fresh_symbol(order.active_id, order.product)
            self._validate_stake(order.amount, balance, symbol)
            if order.duration_minutes * 60 not in symbol.expiration_seconds:
                raise BrokerError("INVALID_EXPIRATION", "This duration is no longer available.")
            now = self.transport.clock.now()
            validate_expiration(symbol, order.expiration, now)
            order = self.journal.claim(order.local_id, str(uuid.uuid4()), now)
            log.info("Submitting order local_id=%s request_id=%s", order.local_id, order.request_id)
            try:
                response = await self.transport.request(
                    order_body(order), expected={"option", "result"}, request_id=order.request_id
                )
                if not isinstance(response, dict) or not response.get("id"):
                    return self._unknown(order)
                broker_id = identifier(response["id"])
                current = self.journal.get(order.local_id)
                if current.state is OrderState.SETTLED:
                    return current
                if current.broker_id and current.broker_id != broker_id:
                    return self._unknown(current)
                self._balance_dirty = True
                return self.journal.save(
                    replace(
                        current, state=OrderState.ACCEPTED, broker_id=broker_id, error_code=None
                    )
                )
            except asyncio.CancelledError:
                self._unknown(order)
                raise
            except RequestError as error:
                if not error.may_have_sent:
                    return self.journal.save(
                        replace(order, state=OrderState.REJECTED, error_code=error.code)
                    )
                return self._unknown(order)
            except BrokerRejection as error:
                current = self.journal.get(order.local_id)
                if current.broker_id:
                    return current
                return self.journal.save(
                    replace(current, state=OrderState.REJECTED, error_code=error.code)
                )
            except BrokerError:
                # A parser error after transmission cannot prove rejection.
                return self._unknown(order)
            except BaseException:
                self._unknown(order)
                raise

    def _unknown(self, order: Order) -> Order:
        current = self.journal.get(order.local_id)
        if current.state in (OrderState.ACCEPTED, OrderState.SETTLED):
            return current
        return self.journal.save(
            replace(current, state=OrderState.UNKNOWN, error_code="ORDER_STATUS_UNKNOWN")
        )

    def list_orders(self) -> list[Order]:
        if not self.user_id:
            return []
        return self.journal.list(self.user_id)

    async def get_order(self, local_order_id: str, *, reconcile: bool = True) -> Order:
        order = self.journal.get(local_order_id)
        if not self.user_id or order.user_id != self.user_id:
            raise BrokerError(
                "ACCOUNT_UNAVAILABLE", "This order belongs to another account; log in as its owner."
            )
        if not reconcile or order.state in (
            OrderState.PREPARED,
            OrderState.REJECTED,
            OrderState.SETTLED,
        ):
            return order
        self._require_connection()
        history = []
        for offset in range(0, 10000, 100):
            raw = await self.transport.request(
                message(
                    "portfolio.get-history-positions",
                    "2.0",
                    {
                        "user_id": int(self.user_id),
                        "user_balance_id": int(order.balance_id),
                        "instrument_types": [order.product.instrument_type],
                        "offset": offset,
                        "limit": 100,
                    },
                ),
                expected={"history-positions", "result"},
            )
            if not isinstance(raw, dict) or not isinstance(raw.get("positions"), list):
                raise BrokerError(
                    "PROTOCOL_UNSUPPORTED", "Broker history response was not recognized."
                )
            positions = raw["positions"]
            history.extend(positions)
            if len(positions) < 100:
                break
        matches = [p for p in history if self._position_matches(order, p)]
        unique = {str(p.get("external_id", p.get("id"))): p for p in matches}
        if len(unique) == 1:
            return self._apply_position(order, next(iter(unique.values())))
        if len(unique) > 1:
            log.warning("Ambiguous history match local_id=%s", order.local_id)
        return self.journal.get(order.local_id)

    def _position_matches(self, order: Order, position: dict) -> bool:
        if (
            not isinstance(position, dict)
            or position.get("instrument_type") != order.product.instrument_type
        ):
            return False
        if (
            position.get("user_balance_id") is not None
            and str(position["user_balance_id"]) != order.balance_id
        ):
            return False
        if order.broker_id:
            return str(position.get("external_id", "")) == order.broker_id
        # Similar trades are never proof of correlation. Unknown submissions need
        # an echoed request ID; otherwise they remain UNKNOWN for manual review.
        return bool(order.request_id and str(position.get("request_id", "")) == order.request_id)

    def _apply_position(self, order: Order, position: dict) -> Order:
        current = self.journal.get(order.local_id)
        if current.state is OrderState.SETTLED:
            return current
        broker_id = identifier(position.get("external_id", current.broker_id))
        if position.get("status") not in ("closed", "settled"):
            return self.journal.save(
                replace(current, state=OrderState.ACCEPTED, broker_id=broker_id, error_code=None)
            )
        gross = decimal(position.get("close_profit"), optional=True)
        net = decimal(position.get("pnl"), optional=True)
        # Preserve unknown payout semantics; never substitute a guessed result.
        self._balance_dirty = True
        return self.journal.save(
            replace(
                current,
                state=OrderState.SETTLED,
                broker_id=broker_id,
                gross_return=gross,
                net_profit=net,
                error_code=None,
            )
        )

    def _on_event(self, envelope: dict) -> None:
        name = envelope.get("name")
        payload = envelope.get("msg")
        if name in ("balance-changed", "order-changed", "position-changed", "option-closed"):
            self._balance_dirty = True
        if name == "commission-changed":
            self._catalog_mono = 0
        if (
            name in ("authenticated", "profile")
            and payload is False
            and self.state is SessionState.CONNECTED
        ):
            self.state = SessionState.SESSION_EXPIRED
        if not self.user_id:
            return
        failure = broker_failure(payload)
        if failure:
            for order in self.journal.list(self.user_id):
                if order.request_id == str(envelope.get("request_id", "")) and order.state in (
                    OrderState.SUBMITTING,
                    OrderState.UNKNOWN,
                ):
                    self.journal.save(
                        replace(order, state=OrderState.REJECTED, error_code=failure.code)
                    )
            return
        if name == "option" and isinstance(payload, dict) and payload.get("id"):
            for order in self.journal.list(self.user_id):
                if order.request_id == str(envelope.get("request_id", "")) and order.state in (
                    OrderState.SUBMITTING,
                    OrderState.UNKNOWN,
                ):
                    try:
                        self.journal.save(
                            replace(
                                order,
                                state=OrderState.ACCEPTED,
                                broker_id=identifier(payload["id"]),
                                error_code=None,
                            )
                        )
                    except BrokerError:
                        pass
        if name == "position-changed" and isinstance(payload, dict):
            for order in self.journal.list(self.user_id):
                if order.state in (
                    OrderState.SUBMITTING,
                    OrderState.UNKNOWN,
                    OrderState.ACCEPTED,
                ) and self._position_matches(order, payload):
                    try:
                        self._apply_position(order, payload)
                    except BrokerError:
                        log.warning("Unrecognized position event local_id=%s", order.local_id)

    def _on_disconnect(self) -> None:
        if self._closing or self.state in (
            SessionState.AUTHENTICATING,
            SessionState.SESSION_EXPIRED,
        ):
            return
        self.state = SessionState.RECONNECTING
        if self._reconnect_task is None or self._reconnect_task.done():
            self._reconnect_task = asyncio.create_task(self._reconnect(), name="iqoption-reconnect")

    async def _reconnect(self) -> None:
        for attempt in range(5):
            if self._closing:
                return
            await asyncio.sleep(min(2**attempt, 16) + random.random())
            try:
                await self.transport.connect(self.auth.session, self.auth.ssid)
                await self._load_account()
                if self._symbols is not None:
                    await self._refresh_catalog()
                self.state = SessionState.CONNECTED
                return
            except BrokerError as error:
                log.warning("Reconnect failed: attempt=%s code=%s", attempt + 1, error.code)
                if error.code in (
                    "SESSION_EXPIRED",
                    "INVALID_CREDENTIALS",
                    "AUTH_CHALLENGE_REQUIRED",
                    "ACCOUNT_UNAVAILABLE",
                ):
                    self.state = SessionState.SESSION_EXPIRED
                    await self.transport.close()
                    return
        self.state = SessionState.ERROR
        await self.transport.close()

    async def _refresh_loop(self) -> None:
        while not self._closing:
            await asyncio.sleep(5)
            if self.state is not SessionState.CONNECTED:
                continue
            try:
                if self._balance_dirty:
                    await self._load_balances()
                if self._symbols is not None and time.monotonic() - self._catalog_mono > 30:
                    await self._refresh_catalog()
                now = self.transport.clock.now()
                for order in self.list_orders():
                    if (
                        order.state in (OrderState.UNKNOWN, OrderState.ACCEPTED)
                        and now > order.expiration + 2
                    ):
                        await self.get_order(order.local_id)
            except BrokerError as error:
                log.info("Background refresh unavailable: code=%s", error.code)
                if error.code == "SESSION_EXPIRED":
                    self.state = SessionState.SESSION_EXPIRED

    async def logout(self) -> bool:
        self._closing = True
        for task in (self._refresh_task, self._reconnect_task, self._payout_task):
            if task is not None and task is not asyncio.current_task():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        self._refresh_task = self._reconnect_task = self._payout_task = None
        await self.transport.close()
        invalidated = await self.auth.logout()
        self.state = SessionState.DISCONNECTED
        self.user_id = None
        self.selected_balance_id = None
        self.balances = []
        self._real_enabled.clear()
        self._symbols = None
        self.protocol_versions.clear()
        return invalidated

    async def close(self) -> None:
        await self.logout()
        # Transport shutdown wakes an in-flight submission. Let it persist its
        # final/unknown state before closing SQLite for application callers.
        async with self._trade_lock:
            self.journal.close()
