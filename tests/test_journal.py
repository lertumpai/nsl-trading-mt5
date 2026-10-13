import tempfile
import unittest
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

from nsl_iqoption.errors import BrokerError
from nsl_iqoption.journal import OrderJournal
from nsl_iqoption.models import Order, OrderState, Product


class JournalTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "orders.sqlite3"
        self.journal = OrderJournal(self.path)
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
            Decimal("1.01"),
            1,
            2_000_000_040,
            2_000_000_000,
        )

    def tearDown(self):
        self.journal.close()
        self.directory.cleanup()

    def test_round_trip_and_account_filter(self):
        self.journal.save(self.order)
        self.assertEqual(self.journal.get("local-1"), self.order)
        self.assertEqual(self.journal.list("other-user"), [])

    def test_atomic_claim_blocks_duplicate(self):
        self.journal.save(self.order)
        claimed = self.journal.claim("local-1", "request-1", 2_000_000_000)
        self.assertEqual(claimed.state, OrderState.SUBMITTING)
        with self.assertRaises(BrokerError) as caught:
            self.journal.claim("local-1", "request-2", 2_000_000_000)
        self.assertEqual(caught.exception.code, "ORDER_ALREADY_SUBMITTED")

    def test_interrupted_send_recovers_as_unknown(self):
        self.journal.save(self.order)
        self.journal.claim("local-1", "request-1", 2_000_000_000)
        self.journal.close()
        self.journal = OrderJournal(self.path)
        recovered = self.journal.get("local-1")
        self.assertEqual(recovered.state, OrderState.UNKNOWN)
        self.assertEqual(recovered.request_id, "request-1")
        with self.assertRaises(BrokerError):
            self.journal.claim("local-1", "request-2", 2_000_000_000)

    def test_accepted_survives_restart(self):
        self.journal.save(replace(self.order, state=OrderState.ACCEPTED, broker_id="10001"))
        self.journal.close()
        self.journal = OrderJournal(self.path)
        self.assertEqual(self.journal.get("local-1").state, OrderState.ACCEPTED)
