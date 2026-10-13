import unittest
from copy import deepcopy
from decimal import Decimal

from support import FIXTURE

from nsl_iqoption.errors import BrokerError
from nsl_iqoption.models import Product, ServerClock
from nsl_iqoption.protocol import (
    dumps,
    expiration_for,
    loads,
    parse_balances,
    parse_symbols,
    validate_expiration,
)


class ProtocolTests(unittest.TestCase):
    def setUp(self):
        self.symbols = parse_symbols(deepcopy(FIXTURE))
        self.turbo = next(
            s for s in self.symbols if s.active_id == "76" and s.product is Product.TURBO
        )
        self.binary = next(s for s in self.symbols if s.product is Product.BINARY)

    def test_product_metadata_stays_separate(self):
        self.assertEqual(len(self.symbols), 3)
        self.assertEqual(self.turbo.expiration_seconds, (60, 120, 300))
        self.assertEqual(self.binary.expiration_seconds, (900, 1800))
        self.assertTrue(self.turbo.otc)

    def test_initialization_commission_gives_product_specific_payout(self):
        raw = deepcopy(FIXTURE)
        raw["binary"]["actives"]["76"]["option"]["profit"]["commission"] = 20
        raw["turbo"]["actives"]["76"]["option"]["profit"]["commission"] = 12
        symbols = parse_symbols(raw)
        self.assertEqual(symbols[0].payout_percent, Decimal(80))
        self.assertEqual(symbols[1].payout_percent, Decimal(88))

    def test_bad_or_missing_optional_commission_does_not_block_catalog(self):
        for value in (None, "NaN", True, -1, 101, "unsupported"):
            raw = deepcopy(FIXTURE)
            raw["turbo"]["actives"]["76"]["option"]["profit"]["commission"] = value
            symbol = parse_symbols(raw)[1]
            self.assertIsNone(symbol.payout_percent)
            self.assertEqual(symbol.market_status(2_000_000_000), "open")

    def test_missing_schedule_is_unknown(self):
        raw = deepcopy(FIXTURE)
        del raw["turbo"]["actives"]["76"]["schedule"]
        self.assertEqual(parse_symbols(raw)[1].market_status(2_000_000_000), "unknown")

    def test_product_suspension_is_closed(self):
        closed = next(s for s in self.symbols if s.active_id == "1")
        self.assertEqual(closed.market_status(2_000_000_000), "closed")
        self.assertEqual(self.turbo.market_status(2_000_000_000), "open")

    def test_missing_enabled_is_unknown(self):
        raw = deepcopy(FIXTURE)
        del raw["turbo"]["actives"]["76"]["enabled"]
        self.assertEqual(parse_symbols(raw)[1].market_status(2_000_000_000), "unknown")

    def test_schedule_end_is_exclusive(self):
        self.assertEqual(self.turbo.market_status(4_000_000_000), "closed")

    def test_nonpositive_schedule_intervals_do_not_invalidate_catalog(self):
        raw = deepcopy(FIXTURE)
        raw["turbo"]["actives"]["76"]["schedule"] = [
            [2_000_000_300, 2_000_000_200],
            [2_000_000_400, 2_000_000_400],
            [2_000_000_000, 2_000_000_100],
        ]
        symbol = parse_symbols(raw)[1]
        self.assertEqual(symbol.schedule, ((2_000_000_000, 2_000_000_100),))
        self.assertEqual(symbol.market_status(2_000_000_050), "open")
        self.assertEqual(symbol.market_status(2_000_000_250), "closed")

    def test_only_invalid_schedule_windows_never_open_a_market(self):
        raw = deepcopy(FIXTURE)
        raw["turbo"]["actives"]["76"]["schedule"] = [
            [2_000_000_300, 2_000_000_200],
            [2_000_000_400, 2_000_000_400],
        ]
        symbol = parse_symbols(raw)[1]
        self.assertEqual(symbol.schedule, ())
        self.assertEqual(symbol.market_status(2_000_000_250), "closed")

    def test_unknown_initialization_is_not_empty_catalog(self):
        with self.assertRaises(BrokerError):
            parse_symbols({"unexpected": {}})
        self.assertEqual(parse_symbols({"binary": {"actives": {}}, "turbo": {"actives": {}}}), ())

    def test_ambiguous_expiration_keys_are_not_minutes(self):
        raw = deepcopy(FIXTURE)
        raw["turbo"]["actives"]["76"]["option"]["expiration_times"] = {"1": {}, "5": {}}
        self.assertEqual(parse_symbols(raw)[1].expiration_seconds, ())

    def test_turbo_cutoff_boundary(self):
        base = 1_999_999_980  # Minute-aligned UTC epoch.
        self.assertEqual(base % 60, 0)
        self.assertEqual(expiration_for(self.turbo, 1, base + 29), base + 60)
        self.assertEqual(expiration_for(self.turbo, 1, base + 30), base + 120)
        self.assertEqual(expiration_for(self.turbo, 1, base + 31), base + 120)

    def test_binary_cutoff_rejects_instead_of_substituting(self):
        base = 2_000_000_000 // 900 * 900
        self.assertEqual(expiration_for(self.binary, 15, base + 100), base + 900)
        with self.assertRaises(BrokerError):
            expiration_for(self.binary, 15, base + 700)

    def test_non_metadata_duration_rejected(self):
        with self.assertRaises(BrokerError):
            expiration_for(self.turbo, 3, 2_000_000_000)

    def test_elapsed_purchase_window_rejected(self):
        with self.assertRaises(BrokerError):
            validate_expiration(self.turbo, 2_000_000_040, 2_000_000_030)

    def test_exact_decimal_json_and_large_ids(self):
        payload = {"price": Decimal("0.10000001"), "id": 9007199254740993}
        self.assertEqual(loads(dumps(payload)), payload)
        self.assertIn('"price":0.10000001', dumps(payload))
        with self.assertRaises(ValueError):
            dumps({"price": float("nan")})

    def test_balance_precision_and_unknown_modes(self):
        balances = parse_balances(
            [
                {"id": 10, "type": 4, "currency": "JPY", "amount": "100"},
                {"id": 11, "type": 2, "currency": "XXX", "amount": "10"},
            ]
        )
        self.assertEqual(balances[0].currency_digits, 0)
        self.assertEqual(balances[1].mode, "unsupported")
        self.assertIsNone(balances[1].currency_digits)

    def test_balance_infinite_and_duplicate_ids_rejected(self):
        raw = [{"id": 10, "type": 4, "currency": "USD", "amount": "Infinity"}]
        with self.assertRaises(BrokerError):
            parse_balances(raw)
        raw[0]["amount"] = "10"
        with self.assertRaises(BrokerError):
            parse_balances(raw * 2)

    def test_clock_uses_monotonic_and_rejects_stale_samples(self):
        ticks = [0.0]
        clock = ServerClock(monotonic=lambda: ticks[0])
        with self.assertRaises(BrokerError):
            clock.now()
        clock.update(2_000_000_000_000)
        ticks[0] = 2.0
        self.assertEqual(clock.now(), 2_000_000_002)
        ticks[0] = 11.0
        with self.assertRaises(BrokerError):
            clock.now()
        clock.reset()
        with self.assertRaises(BrokerError):
            clock.now()
