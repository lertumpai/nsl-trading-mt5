"""All broker and SQLite access stays on one background asyncio thread."""

import asyncio
import logging
import time
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from .client import IQOptionClient
from .errors import BrokerError
from .gui_state import DesktopSnapshot
from .transport import WebSocketTransport

log = logging.getLogger(__name__)


class BrokerWorker(QThread):
    ready = Signal()
    snapshot = Signal(object)
    completed = Signal(str, object, object)

    def __init__(
        self,
        journal_path: Path,
        *,
        auth_style: str = "modern",
        timeout: float = 10,
        client_factory: Callable[[], IQOptionClient] | None = None,
    ):
        super().__init__()
        self._factory = client_factory or (
            lambda: IQOptionClient(
                journal_path,
                transport=WebSocketTransport(auth_style=auth_style, request_timeout=timeout),
            )
        )
        self._loop: asyncio.AbstractEventLoop | None = None
        self._queue: asyncio.Queue | None = None
        self._active: asyncio.Task | None = None
        self._stopping = False
        self._client: IQOptionClient | None = None

    def request(self, operation: str, **payload: object) -> bool:
        if self._loop is None or self._stopping:
            return False
        try:
            self._loop.call_soon_threadsafe(self._enqueue, operation, payload)
        except RuntimeError:
            payload.clear()
            return False
        return True

    def _enqueue(self, operation: str, payload: dict) -> None:
        if self._stopping:
            payload.clear()
            return
        self._queue.put_nowait((operation, payload))

    def shutdown(self) -> None:
        self._stopping = True
        if self._loop is not None:
            try:
                self._loop.call_soon_threadsafe(self._stop_in_loop)
            except RuntimeError:
                pass

    def _stop_in_loop(self) -> None:
        while not self._queue.empty():
            _, payload = self._queue.get_nowait()
            payload.clear()
        self._queue.put_nowait(("shutdown", {}))
        if self._active is not None:
            self._active.cancel()

    def run(self) -> None:
        try:
            asyncio.run(self._main())
        except Exception as error:  # noqa: BLE001 - Do not expose secrets in boundary tracebacks.
            log.error("Desktop worker stopped: type=%s", type(error).__name__)
            self.completed.emit(
                "startup",
                None,
                (
                    "INTERNAL_ERROR",
                    "The broker worker could not start. Check that the order journal is writable.",
                ),
            )
        finally:
            self._loop = None

    async def _main(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._queue = asyncio.Queue()
        self._client = self._factory()
        if self._stopping:
            await self._client.close()
            return
        self._publish()
        self.ready.emit()
        poll = asyncio.create_task(self._poll())
        try:
            while not self._stopping:
                operation, payload = await self._queue.get()
                if operation == "shutdown":
                    break
                self._active = asyncio.create_task(self._perform(operation, payload))
                try:
                    value = await self._active
                    self._publish()
                    self.completed.emit(operation, value, None)
                except asyncio.CancelledError:
                    if self._stopping:
                        break
                    self.completed.emit(operation, None, ("CANCELED", "Operation canceled."))
                except BrokerError as error:
                    self._publish()
                    self.completed.emit(operation, None, (error.code, error.message))
                except Exception as error:  # noqa: BLE001 - Sanitize errors crossing into the UI.
                    log.error(
                        "Desktop operation failed: operation=%s type=%s",
                        operation,
                        type(error).__name__,
                    )
                    self._publish()
                    self.completed.emit(
                        operation,
                        None,
                        (
                            "INTERNAL_ERROR",
                            "The operation could not complete. Reconnect and check the order's recorded status before retrying.",
                        ),
                    )
                finally:
                    payload.clear()
                    self._active = None
        finally:
            poll.cancel()
            try:
                await poll
            except asyncio.CancelledError:
                pass
            await self._client.close()

    async def _perform(self, operation: str, payload: dict) -> object:
        client = self._client
        if operation == "login":
            return await client.login(payload.pop("identifier"), payload.pop("secret"))
        if operation == "logout":
            return await client.logout()
        if operation == "symbols":
            return await client.list_symbols(refresh=True)
        if operation == "balances":
            return await client.list_balances()
        if operation == "balance":
            return client.select_balance(payload["balance_id"], enable_real=payload["enable_real"])
        if operation == "prepare":
            return await client.prepare_order(
                payload["order_input"], acknowledge_unknown=payload["acknowledge_unknown"]
            )
        if operation == "submit":
            return await client.submit_order(
                payload["local_id"], acknowledge_unknown=payload["acknowledge_unknown"]
            )
        if operation == "reconcile":
            return await client.get_order(payload["local_id"])
        raise BrokerError("INVALID_INPUT", "Unknown desktop operation.")

    def _publish(self) -> None:
        client = self._client
        catalog = client.cached_symbols()
        try:
            server_time = client.transport.clock.now()
        except BrokerError:
            server_time = None
        self.snapshot.emit(
            DesktopSnapshot(
                state=client.state,
                user_id=client.user_id,
                selected_balance_id=client.selected_balance_id,
                balances=tuple(client.balances),
                symbols=catalog.symbols if catalog else (),
                orders=tuple(client.list_orders()),
                catalog_at=catalog.refreshed_at if catalog else None,
                catalog_stale=catalog.stale if catalog else True,
                server_time=server_time,
                sampled_at=time.monotonic(),
            )
        )

    async def _poll(self) -> None:
        while True:
            await asyncio.sleep(1)
            self._publish()
