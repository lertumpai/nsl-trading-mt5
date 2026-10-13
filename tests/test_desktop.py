import asyncio
import os
import threading
import time
import unittest
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtCore import QObject, QTimer, Signal
    from PySide6.QtWidgets import QApplication, QLineEdit, QMessageBox

    from nsl_iqoption.desktop import TradingWindow
    from nsl_iqoption.gui_widgets import ReviewDialog
    from nsl_iqoption.gui_worker import BrokerWorker

    QT_AVAILABLE = True
except ModuleNotFoundError:
    QT_AVAILABLE = False

from support import FIXTURE

from nsl_iqoption.errors import BrokerError
from nsl_iqoption.gui_state import DesktopSnapshot
from nsl_iqoption.models import (
    Balance,
    Order,
    OrderState,
    Product,
    Session,
    SessionState,
)
from nsl_iqoption.protocol import parse_symbols

if QT_AVAILABLE:

    class FakeWorker(QObject):
        ready = Signal()
        snapshot = Signal(object)
        completed = Signal(str, object, object)
        finished = Signal()

        def __init__(self):
            super().__init__()
            self.requests = []

        def request(self, operation, **payload):
            self.requests.append((operation, payload))
            return True

        def isRunning(self):
            return False


def app():
    instance = QApplication.instance() or QApplication([])
    instance.setStyle("Fusion")
    return instance


@unittest.skipUnless(QT_AVAILABLE, "Install the gui extra to run Qt desktop tests")
class DesktopTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = app()

    def setUp(self):
        self.worker = FakeWorker()
        self.window = TradingWindow(self.worker)
        self.window.show()
        self.worker.ready.emit()
        self.connected = DesktopSnapshot(
            state=SessionState.CONNECTED,
            user_id="123",
            selected_balance_id="10",
            balances=(
                Balance("10", "practice", "USD", Decimal(100), 2),
                Balance("20", "real", "USD", Decimal(50), 2),
            ),
            symbols=parse_symbols(FIXTURE),
            server_time=2_000_000_000,
            sampled_at=time.monotonic(),
            catalog_at=time.time(),
            catalog_stale=False,
        )
        self.order = Order(
            "local-1",
            "123",
            "10",
            "practice",
            "USD",
            "76",
            "EURUSD-OTC",
            Product.TURBO,
            "call",
            Decimal("1.00"),
            1,
            2_000_000_040,
            2_000_000_000,
        )
        self.app.processEvents()

    def tearDown(self):
        self.window.close()
        self.app.processEvents()

    def connect(self):
        self.worker.snapshot.emit(self.connected)
        self.app.processEvents()

    def ticket(self):
        self.connect()
        self.window.market_table.selectRow(1)
        self.window.call_button.click()
        self.window.amount.setText("1.00")
        self.app.processEvents()

    def test_initial_state_cannot_prepare_or_submit(self):
        self.assertFalse(self.window.review_button.isEnabled())
        self.assertFalse(self.window.refresh_markets.isEnabled())
        self.assertEqual(self.worker.requests, [])

    def test_login_masks_and_clears_password(self):
        self.assertEqual(self.window.password.echoMode(), QLineEdit.EchoMode.Password)
        self.window.identifier.setText("synthetic@example.invalid")
        self.window.password.setText("synthetic-password")
        self.window.login_button.click()
        self.assertEqual(self.window.password.text(), "")
        self.assertEqual(self.worker.requests[0][0], "login")
        self.assertNotIn("synthetic-password", self.window.notice.text())

    def test_product_selection_uses_separate_binary_turbo_metadata(self):
        self.connect()
        self.window.market_table.selectRow(0)
        self.assertEqual(self.window._selected_symbol().product, Product.BINARY)
        self.assertEqual(self.window.duration.itemData(0), 15)
        self.window.market_table.selectRow(1)
        self.assertEqual(self.window._selected_symbol().product, Product.TURBO)
        self.assertEqual(self.window.duration.itemData(0), 1)

    def test_search_and_otc_filters_do_not_change_broker_data(self):
        self.connect()
        self.window.search.setText("EURUSD-OTC")
        self.assertEqual(self.window.market_table.rowCount(), 2)
        self.window.otc_filter.setCurrentIndex(2)
        self.assertEqual(self.window.market_table.rowCount(), 0)
        self.assertEqual(len(self.window.snapshot.symbols), 3)

    def test_closed_product_cannot_prepare(self):
        self.connect()
        self.window.market_table.selectRow(2)
        self.assertFalse(self.window.review_button.isEnabled())
        self.assertFalse(self.window.amount.isEnabled())

    def test_ticket_prepares_but_does_not_submit(self):
        self.ticket()
        self.assertTrue(self.window.review_button.isEnabled())
        self.window.review_button.click()
        self.assertEqual([name for name, _ in self.worker.requests], ["prepare"])
        order_input = self.worker.requests[0][1]["order_input"]
        self.assertEqual(order_input.product, Product.TURBO)
        self.assertEqual(order_input.amount, Decimal("1.00"))

    def test_cancel_review_sends_no_order(self):
        self.connect()
        QTimer.singleShot(0, lambda: self.window._review.cancel_button.click())
        self.window._completed("prepare", self.order, None)
        self.assertEqual(self.worker.requests, [])

    def test_confirm_review_submits_exact_intent_once(self):
        self.connect()
        QTimer.singleShot(0, lambda: self.window._review.submit_button.click())
        self.window._completed("prepare", self.order, None)
        self.assertEqual(
            self.worker.requests,
            [("submit", {"local_id": "local-1", "acknowledge_unknown": False})],
        )
        self.assertTrue(self.window._busy)
        self.assertFalse(self.window.review_button.isEnabled())

    def test_expired_review_disables_submission(self):
        self.connect()
        dialog = ReviewDialog(
            replace(self.order, expiration=2_000_000_020),
            30,
            lambda: self.window.snapshot,
            self.window,
        )
        self.assertFalse(dialog.submit_button.isEnabled())
        dialog.close()

    def test_real_balance_requires_explicit_enable(self):
        self.connect()
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.No):
            self.window.balance_combo.setCurrentIndex(1)
        self.assertEqual(self.worker.requests, [])
        self.assertEqual(self.window.balance_combo.currentData(), "10")
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            self.window.balance_combo.setCurrentIndex(1)
        self.assertEqual(
            self.worker.requests, [("balance", {"balance_id": "20", "enable_real": True})]
        )

    def test_unknown_order_gate_survives_tab_changes(self):
        self.ticket()
        self.window._navigate(1)
        unknown = replace(self.order, state=OrderState.UNKNOWN)
        self.worker.snapshot.emit(replace(self.connected, orders=(unknown,)))
        self.window._navigate(0)
        self.assertFalse(self.window.review_button.isEnabled())
        self.window.ack_unknown.setChecked(True)
        self.assertTrue(self.window.review_button.isEnabled())

    def test_disconnect_disables_ticket(self):
        self.ticket()
        self.worker.snapshot.emit(replace(self.connected, state=SessionState.RECONNECTING))
        self.assertFalse(self.window.review_button.isEnabled())
        self.assertFalse(self.window.refresh_markets.isEnabled())

    def test_errors_are_visible_and_controls_are_released(self):
        self.window._busy = True
        self.window._completed("login", None, ("INVALID_CREDENTIALS", "Login failed."))
        self.assertFalse(self.window._busy)
        self.assertIn("Login failed", self.window.notice.text())
        self.assertTrue(self.window.login_button.isEnabled())

    def test_failed_balance_selection_restores_actual_account(self):
        self.connect()
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            self.window.balance_combo.setCurrentIndex(1)
        self.window._completed("balance", None, ("ACCOUNT_UNAVAILABLE", "Balance unavailable."))
        self.assertEqual(self.window.balance_combo.currentData(), "10")

    def test_unknown_acknowledgement_cannot_cross_balances(self):
        self.connect()
        frozen_real_order = replace(self.order, balance_id="20", mode="real")
        QTimer.singleShot(0, lambda: self.window._review.submit_button.click())
        self.window._show_review(frozen_real_order, acknowledge_unknown=True)
        self.assertFalse(self.worker.requests[0][1]["acknowledge_unknown"])


class ThreadBoundClient:
    """Fails if the GUI touches broker models/journal from its own thread."""

    def __init__(self):
        self.owner = threading.get_ident()
        self.state = SessionState.DISCONNECTED
        self.user_id = None
        self.selected_balance_id = None
        self.balances = []
        self.transport = SimpleNamespace(clock=SimpleNamespace(now=lambda: 2_000_000_000))
        self.closed = False

    def check_thread(self):
        if threading.get_ident() != self.owner:
            raise RuntimeError("Broker accessed from UI thread")

    def cached_symbols(self):
        self.check_thread()

    def list_orders(self):
        self.check_thread()
        return []

    async def login(self, identifier, secret):
        self.check_thread()
        await asyncio.sleep(30 if secret == "slow" else 0.01)
        if secret == "invalid":
            raise BrokerError("INVALID_CREDENTIALS", "Login failed.")
        self.state = SessionState.CONNECTED
        self.user_id = "123"
        return Session("123", None, self.state)

    async def close(self):
        self.check_thread()
        self.closed = True


@unittest.skipUnless(QT_AVAILABLE, "Install the gui extra to run Qt worker tests")
class WorkerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = app()

    def setUp(self):
        self.clients = []

        def factory():
            client = ThreadBoundClient()
            self.clients.append(client)
            return client

        self.worker = BrokerWorker(Path("unused-test-path"), client_factory=factory)
        self.ready = False
        self.results = []
        self.worker.ready.connect(self.mark_ready)
        self.worker.completed.connect(lambda *values: self.results.append(values))
        self.worker.start()
        self.wait_until(lambda: self.ready)

    def mark_ready(self):
        self.ready = True

    def wait_until(self, condition):
        deadline = time.monotonic() + 3
        while not condition() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.002)
        self.assertTrue(condition())

    def tearDown(self):
        self.worker.shutdown()
        self.worker.wait(3000)
        self.assertFalse(self.worker.isRunning())
        self.assertTrue(self.clients[0].closed)

    def test_login_runs_on_worker_thread(self):
        self.worker.request("login", identifier="synthetic", secret="synthetic")
        self.wait_until(lambda: bool(self.results))
        self.assertNotEqual(self.clients[0].owner, threading.get_ident())
        self.assertIsNone(self.results[0][2])

    def test_errors_cross_thread_as_sanitized_values(self):
        self.worker.request("login", identifier="synthetic", secret="invalid")
        self.wait_until(lambda: bool(self.results))
        self.assertEqual(self.results[0][2], ("INVALID_CREDENTIALS", "Login failed."))
        self.assertNotIn("invalid", repr(self.results[0][1]))

    def test_shutdown_cancels_pending_request_and_closes_client(self):
        self.worker.request("login", identifier="synthetic", secret="slow")
        self.wait_until(lambda: self.worker._active is not None)
        self.worker.shutdown()
        self.worker.wait(3000)
        self.assertFalse(self.worker.isRunning())
        self.assertTrue(self.clients[0].closed)
