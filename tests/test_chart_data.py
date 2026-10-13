import unittest
from copy import deepcopy
from decimal import Decimal

from nsl_iqoption.chart_data import bollinger_bands, moving_average, relative_strength
from nsl_iqoption.errors import BrokerError
from nsl_iqoption.protocol import parse_candles


class CandleTests(unittest.TestCase):
    def setUp(self):
        self.row = {
            "from": 1_999_999_920,
            "to": 1_999_999_980,
            "open": "1.10",
            "close": "1.12",
            "min": "1.09",
            "max": "1.13",
            "volume": 12,
        }

    def test_candles_keep_decimal_ohlc_and_sort_by_time(self):
        earlier = dict(self.row, **{"from": self.row["from"] - 60, "to": self.row["to"] - 60})
        candles = parse_candles({"candles": [self.row, earlier]}, 60)
        self.assertEqual(candles[0].start, earlier["from"])
        self.assertEqual(candles[1].close, Decimal("1.12"))
        self.assertEqual(candles[1].volume, Decimal(12))
        self.assertEqual(parse_candles({"candles": []}, 60), ())

    def test_malformed_ohlc_and_wrong_intervals_are_rejected(self):
        for field, value in (
            ("open", "NaN"),
            ("close", "Infinity"),
            ("min", "1.11"),
            ("max", "1.11"),
            ("to", self.row["to"] + 1),
            ("from", True),
            ("volume", -1),
        ):
            with self.subTest(field=field), self.assertRaises(BrokerError):
                parse_candles({"candles": [dict(self.row, **{field: value})]}, 60)
        missing = deepcopy(self.row)
        del missing["close"]
        for payload in ({}, {"candles": [missing]}, {"candles": [self.row, self.row]}):
            with self.assertRaises(BrokerError):
                parse_candles(payload, 60)

    def test_indicator_warmup_and_known_values(self):
        self.assertEqual(moving_average([1, 2, 3, 4], 3), [None, None, 2, 3])
        upper, lower = bollinger_bands([10.0] * 20)
        self.assertEqual(upper, [None] * 19 + [10])
        self.assertEqual(lower, [None] * 19 + [10])
        self.assertEqual(relative_strength([10] * 15), [None] * 14 + [50])
        self.assertEqual(relative_strength(list(range(15)))[-1], 100)
        self.assertEqual(relative_strength(list(range(15, 0, -1)))[-1], 0)
