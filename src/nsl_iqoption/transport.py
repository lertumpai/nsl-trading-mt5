"""A single reader dispatches replies before application events can race them."""

import asyncio
import logging
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass

import aiohttp

from .errors import BrokerError, RequestError, broker_failure
from .models import ServerClock
from .protocol import WS_URL, dumps, loads, success_acknowledgement

log = logging.getLogger(__name__)


@dataclass
class Pending:
    future: asyncio.Future
    expected: frozenset[str]
    sent: bool = False


class WebSocketTransport:
    def __init__(
        self,
        *,
        url: str = WS_URL,
        request_timeout: float = 10.0,
        connect_timeout: float = 15.0,
        auth_style: str = "modern",
        clock: ServerClock | None = None,
    ):
        if auth_style not in ("modern", "legacy"):
            raise ValueError("auth_style must be modern or legacy")
        self.url = url
        self.request_timeout = request_timeout
        self.connect_timeout = connect_timeout
        self.auth_style = auth_style
        self.clock = clock or ServerClock()
        self.on_event: Callable[[dict], None] = lambda _: None
        self.on_disconnect: Callable[[], None] = lambda: None
        self.ws: aiohttp.ClientWebSocketResponse | None = None
        self._reader: asyncio.Task | None = None
        self._pending: dict[str, Pending] = {}
        self._auth_future: asyncio.Future | None = None
        self._auth_request_id: str | None = None
        self._closing = False
        self._started_at = time.monotonic()
        self._time_ready = asyncio.Event()

    @property
    def connected(self) -> bool:
        return self.ws is not None and not self.ws.closed

    async def connect(self, session: aiohttp.ClientSession, ssid: str) -> None:
        await self.close()
        self._closing = False
        self.clock.reset()
        self._time_ready.clear()
        self._started_at = time.monotonic()
        try:
            async with asyncio.timeout(self.connect_timeout):
                self.ws = await session.ws_connect(
                    self.url,
                    origin="https://iqoption.com",
                    heartbeat=30,
                    max_msg_size=16 * 1024 * 1024,
                )
        except (TimeoutError, aiohttp.ClientError):
            raise BrokerError(
                "NETWORK_TIMEOUT", "Could not open a secure broker WebSocket."
            ) from None
        self._auth_future = asyncio.get_running_loop().create_future()
        self._auth_request_id = str(uuid.uuid4())
        self._reader = asyncio.create_task(self._read(self.ws), name="iqoption-reader")
        envelope = {
            "name": "authenticate",
            "request_id": self._auth_request_id,
            "msg": {"ssid": ssid, "protocol": 3, "session_id": "", "client_session_id": ""},
        }
        if self.auth_style == "legacy":
            envelope = {"name": "ssid", "request_id": self._auth_request_id, "msg": ssid}
        try:
            async with asyncio.timeout(self.request_timeout):
                await self.ws.send_str(dumps(envelope))
                await self._auth_future
                await self.ws.send_str(
                    dumps(
                        {
                            "name": "setOptions",
                            "request_id": str(uuid.uuid4()),
                            "msg": {"sendResults": True},
                        }
                    )
                )
        except TimeoutError:
            await self.close()
            raise BrokerError(
                "NETWORK_TIMEOUT",
                "WebSocket authentication did not complete; try the documented protocol settings.",
            ) from None
        except (aiohttp.ClientError, OSError):
            await self.close()
            raise BrokerError(
                "NETWORK_TIMEOUT", "WebSocket disconnected during authentication."
            ) from None
        except BaseException:
            await self.close()
            raise
        finally:
            self._auth_future = None

    async def wait_for_time(self) -> None:
        try:
            await asyncio.wait_for(self._time_ready.wait(), self.request_timeout)
            self.clock.now()
        except TimeoutError:
            raise BrokerError(
                "METADATA_STALE", "Broker sent no fresh time synchronization."
            ) from None

    async def request(
        self,
        msg: dict,
        *,
        expected: set[str] | frozenset[str],
        request_id: str | None = None,
        envelope_name: str = "sendMessage",
    ) -> object:
        request_id = request_id or str(uuid.uuid4())
        if request_id in self._pending:
            raise BrokerError("PROTOCOL_UNSUPPORTED", "Request identifier is already in use.")
        future = asyncio.get_running_loop().create_future()
        pending = Pending(future, frozenset(expected))
        self._pending[request_id] = pending
        try:
            wire = dumps(
                {
                    "name": envelope_name,
                    "request_id": request_id,
                    "local_time": int(time.monotonic() - self._started_at),
                    "msg": msg,
                }
            )
            if not self.connected:
                raise RequestError(
                    "NETWORK_TIMEOUT", "WebSocket is disconnected.", may_have_sent=False
                )
            async with asyncio.timeout(self.request_timeout):
                # From this point a failed await cannot prove that nothing was sent.
                pending.sent = True
                await self.ws.send_str(wire)
                return await future
        except TimeoutError:
            raise RequestError(
                "NETWORK_TIMEOUT", "Broker response timed out.", may_have_sent=pending.sent
            ) from None
        except (aiohttp.ClientError, OSError, RuntimeError):
            raise RequestError(
                "NETWORK_TIMEOUT", "WebSocket transmission failed.", may_have_sent=pending.sent
            ) from None
        finally:
            self._pending.pop(request_id, None)
            if not future.done():
                future.cancel()
            elif not future.cancelled():
                future.exception()  # Retrieve any disconnect exception during send.

    async def subscribe(self, name: str, version: str, filters: dict) -> None:
        await self.request(
            {"name": name, "version": version, "params": {"routingFilters": filters}},
            expected={"result"},
            envelope_name="subscribeMessage",
        )

    def dispatch(self, envelope: dict) -> None:
        name = envelope.get("name")
        payload = envelope.get("msg")
        request_id = str(envelope.get("request_id", ""))
        if name == "timeSync":
            self.clock.update(payload)
            try:
                self.clock.now()
                self._time_ready.set()
            except BrokerError:
                pass
        auth = self._auth_future
        if auth is not None and not auth.done():
            if name == "authenticated" or (
                name == "result" and request_id == self._auth_request_id
            ):
                if success_acknowledgement(payload):
                    auth.set_result(True)
                elif payload is False or broker_failure(payload):
                    auth.set_exception(
                        BrokerError("SESSION_EXPIRED", "Broker rejected the session token.")
                    )
            elif (
                self.auth_style == "legacy"
                and name == "profile"
                and isinstance(payload, dict)
                and (payload.get("user_id") or payload.get("id"))
            ):
                auth.set_result(True)
        pending = self._pending.get(request_id)
        if pending is not None and not pending.future.done():
            failure = broker_failure(payload)
            if failure:
                pending.future.set_exception(failure)
            elif name in pending.expected and not (
                name == "result"
                and success_acknowledgement(payload)
                and pending.expected != frozenset({"result"})
            ):
                pending.future.set_result(payload)
            # Generic boolean/object acknowledgements must not resolve data requests.
        self.on_event(envelope)

    async def _read(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        try:
            async for frame in ws:
                if frame.type is aiohttp.WSMsgType.TEXT:
                    try:
                        envelope = loads(frame.data)
                    except ValueError:
                        log.warning("Ignored malformed broker JSON")
                        continue
                    if not isinstance(envelope, dict) or not isinstance(envelope.get("name"), str):
                        continue
                    if envelope["name"] == "heartbeat":
                        await ws.send_str(
                            dumps(
                                {
                                    "name": "heartbeat",
                                    "msg": {
                                        "userTime": str(int(time.time() * 1000)),
                                        "heartbeatTime": str(envelope.get("msg", "")),
                                    },
                                }
                            )
                        )
                    self.dispatch(envelope)
                elif frame.type in (
                    aiohttp.WSMsgType.ERROR,
                    aiohttp.WSMsgType.CLOSE,
                    aiohttp.WSMsgType.CLOSED,
                ):
                    break
        except asyncio.CancelledError:
            raise
        except (aiohttp.ClientError, OSError, ValueError, TypeError):
            log.warning("Broker reader disconnected")
        finally:
            for pending in list(self._pending.values()):
                if not pending.future.done():
                    pending.future.set_exception(
                        RequestError(
                            "NETWORK_TIMEOUT",
                            "Broker disconnected before its response.",
                            may_have_sent=pending.sent,
                        )
                    )
            if self._auth_future is not None and not self._auth_future.done():
                self._auth_future.set_exception(
                    BrokerError("NETWORK_TIMEOUT", "Broker disconnected during authentication.")
                )
            if not self._closing:
                self.clock.reset()
                self.on_disconnect()

    async def close(self) -> None:
        self._closing = True
        if self._reader is not None:
            self._reader.cancel()
            try:
                await self._reader
            except asyncio.CancelledError:
                pass
            self._reader = None
        if self.ws is not None:
            await self.ws.close()
            self.ws = None
        self.clock.reset()
