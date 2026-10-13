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
    Candle,
    CandleSeries,
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
        chart_updated = Signal(str, int, object, object)
        finished = Signal()

        def __init__(self):
            super().__init__()
            self.requests = []
            self.chart_requests = []

        def request(self, operation, **payload):
            if operation == "chart":
                self.chart_requests.append(payload)
                return True
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
        self.assertEqual(
            [self.window.duration.itemData(i) for i in range(self.window.duration.count())],
            [1, 2, 5, 15, 30],
        )
        self.assertEqual(self.window._ticket_symbol().product, Product.TURBO)
        self.window.duration.setCurrentIndex(self.window.duration.findData(15))
        self.assertEqual(self.window._ticket_symbol().product, Product.BINARY)
        self.window.market_table.selectRow(1)
        self.assertEqual(self.window._selected_symbol().product, Product.TURBO)
        self.assertEqual(self.window.duration.itemData(0), 1)

    def test_one_minute_from_binary_row_routes_to_turbo(self):
        self.connect()
        self.window.market_table.selectRow(0)
        self.window.call_button.click()
        self.window.amount.setText("1.00")
        self.window.review_button.click()
        order_input = self.worker.requests[-1][1]["order_input"]
        self.assertEqual((order_input.duration_minutes, order_input.product), (1, Product.TURBO))

    def test_chart_timeframes_do_not_change_trade_expiry(self):
        self.ticket()
        for seconds in (60, 300, 900, 3600, 86400):
            self.window.timeframe_buttons[seconds].click()
            self.assertEqual(
                self.worker.chart_requests[-1], {"active_id": "76", "timeframe": seconds}
            )
            self.assertEqual(self.window.duration.currentData(), 1)
            self.assertEqual(self.window._ticket_symbol().product, Product.TURBO)
        self.assertEqual(self.worker.requests, [])

    def test_old_chart_reply_is_ignored_and_disconnect_clears_data(self):
        self.ticket()
        series = CandleSeries(
            "76",
            60,
            (Candle(1_999_999_920, 1_999_999_980, Decimal(1), Decimal(2), Decimal(1), Decimal(2)),),
            2_000_000_000,
        )
        self.worker.chart_updated.emit("76", 60, series, None)
        self.assertIs(self.window.chart.series, series)
        self.window.timeframe_buttons[300].click()
        self.worker.chart_updated.emit("76", 60, series, None)
        self.assertIsNone(self.window.chart.series)
        self.worker.snapshot.emit(replace(self.connected, state=SessionState.RECONNECTING))
        self.assertIsNone(self.window.chart.series)
        self.assertEqual(self.worker.chart_requests[-1], {"active_id": None})

    def test_chart_error_does_not_disable_trading_or_release_trade_request(self):
        self.ticket()
        self.window._busy = True
        self.worker.chart_updated.emit("76", 60, None, ("NETWORK_TIMEOUT", "Timed out."))
        self.assertTrue(self.window._busy)
        self.assertIn("Timed out", self.window.chart_status.text())
        self.window._busy = False
        self.window._controls()
        self.assertTrue(self.window.review_button.isEnabled())

    def test_payout_and_amount_shortcuts_use_product_and_currency(self):
        self.connect()
        symbols = tuple(
            replace(s, payout_percent=Decimal(85) if s.product is Product.TURBO else Decimal(70))
            for s in self.connected.symbols
        )
        self.worker.snapshot.emit(replace(self.connected, symbols=symbols))
        self.window.market_table.selectRow(0)
        self.window.amount.setText("1.00")
        self.assertIn("0.85 USD", self.window.profit_hint.text())
        self.window.duration.setCurrentIndex(self.window.duration.findData(15))
        self.assertIn("0.70 USD", self.window.profit_hint.text())
        self.window._adjust_amount("double")
        self.assertEqual(self.window.amount.text(), "2.00")
        self.worker.snapshot.emit(replace(self.connected, catalog_stale=True))
        self.assertEqual(self.window.profit_hint.text(), "Profit if successful: —")

    def test_amount_shortcuts_round_inside_broker_limits(self):
        self.ticket()
        symbols = tuple(
            replace(s, min_stake=Decimal("1.001"), max_stake=Decimal("4.999"))
            for s in self.connected.symbols
        )
        self.worker.snapshot.emit(replace(self.connected, symbols=symbols))
        self.window._adjust_amount("half")
        self.assertEqual(self.window.amount.text(), "1.01")
        self.window.amount.setText("4.99")
        self.window._adjust_amount("double")
        self.assertEqual(self.window.amount.text(), "4.99")

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
        self.chart_calls = []
        self.canceled_charts = []

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

    async def get_candles(self, active_id, timeframe):
        self.check_thread()
        self.chart_calls.append((active_id, timeframe))
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            self.canceled_charts.append((active_id, timeframe))
            raise

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

    def test_slow_chart_does_not_block_commands_and_latest_watch_cancels_previous(self):
        self.worker.request("login", identifier="synthetic", secret="synthetic")
        self.wait_until(lambda: len(self.results) == 1)
        self.worker.request("chart", active_id="76", timeframe=60)
        self.wait_until(lambda: bool(self.clients[0].chart_calls))
        self.worker.request("login", identifier="synthetic", secret="synthetic")
        self.wait_until(lambda: len(self.results) == 2)
        self.worker.request("chart", active_id="76", timeframe=300)
        self.wait_until(lambda: len(self.clients[0].chart_calls) == 2)
        self.assertEqual(self.clients[0].canceled_charts, [("76", 60)])
        self.worker.request("chart", active_id=None)
        self.wait_until(lambda: len(self.clients[0].canceled_charts) == 2)
        self.assertEqual(self.clients[0].canceled_charts[-1], ("76", 300))

    def test_shutdown_cancels_chart_before_closing_broker(self):
        self.worker.request("login", identifier="synthetic", secret="synthetic")
        self.wait_until(lambda: bool(self.results))
        self.worker.request("chart", active_id="76", timeframe=60)
        self.wait_until(lambda: bool(self.clients[0].chart_calls))
        self.worker.shutdown()
        self.worker.wait(3000)
        self.assertFalse(self.worker.isRunning())
        self.assertEqual(self.clients[0].canceled_charts, [("76", 60)])
