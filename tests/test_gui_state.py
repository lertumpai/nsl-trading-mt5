import time
import unittest
from dataclasses import replace
from decimal import Decimal

from support import FIXTURE

from nsl_iqoption.errors import BrokerError
from nsl_iqoption.gui_state import DesktopSnapshot, review_status, supported_minutes, ticket_input
from nsl_iqoption.models import Balance, Order, OrderState, Product, SessionState
from nsl_iqoption.protocol import parse_symbols


class DesktopStateTests(unittest.TestCase):
    def setUp(self):
        self.turbo = next(
            s for s in parse_symbols(FIXTURE) if s.active_id == "76" and s.product is Product.TURBO
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
        self.snapshot = DesktopSnapshot(
            state=SessionState.CONNECTED,
            user_id="123",
            selected_balance_id="10",
            balances=(Balance("10", "practice", "USD", Decimal(100), 2),),
            server_time=2_000_000_000,
            sampled_at=time.monotonic(),
        )

    def test_ticket_keeps_exact_product_and_decimal_stake(self):
        order_input = ticket_input(self.turbo, " 1.01 ", 1, "put")
        self.assertEqual(order_input.symbol, "76")
        self.assertEqual(order_input.product, Product.TURBO)
        self.assertEqual(order_input.amount, Decimal("1.01"))

    def test_ticket_does_not_invent_durations(self):
        self.assertEqual(supported_minutes(self.turbo), (1, 2, 5))
        with self.assertRaises(BrokerError):
            ticket_input(self.turbo, "1", 3, "call")

    def test_ticket_rejects_invalid_and_nonfinite_amounts(self):
        for amount in ("", "text", "NaN", "Infinity", "-1", "0"):
            with self.subTest(amount=amount), self.assertRaises(BrokerError):
                ticket_input(self.turbo, amount, 1, "call")

    def test_review_uses_purchase_cutoff_not_expiration_alone(self):
        self.assertTrue(review_status(self.order, self.snapshot, 30)[0])
        expired_window = replace(self.snapshot, server_time=2_000_000_015)
        self.assertFalse(review_status(self.order, expired_window, 30)[0])

    def test_disconnected_stale_or_wrong_account_cannot_submit(self):
        for snapshot in (
            replace(self.snapshot, state=SessionState.RECONNECTING),
            replace(self.snapshot, user_id="other"),
            replace(self.snapshot, sampled_at=time.monotonic() - 4),
            replace(self.snapshot, server_time=None),
        ):
            with self.subTest(snapshot=snapshot):
                self.assertFalse(review_status(self.order, snapshot, 30)[0])

    def test_submitted_order_cannot_be_reviewed_again(self):
        self.assertFalse(
            review_status(replace(self.order, state=OrderState.ACCEPTED), self.snapshot, 30)[0]
        )

    def test_balance_selection_change_does_not_rewrite_review(self):
        changed = replace(self.snapshot, selected_balance_id="20")
        self.assertTrue(review_status(self.order, changed, 30)[0])
        self.assertEqual(self.order.balance_id, "10")

    def test_missing_frozen_balance_cannot_submit(self):
        self.assertFalse(review_status(self.order, replace(self.snapshot, balances=()), 30)[0])
