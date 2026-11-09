"""ทดสอบ mt5kit ด้วยข้อมูลจำลอง: python3 -m unittest discover -s tests -v"""
from __future__ import annotations

import json
import re
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).parent))

from mt5kit import adapters as ad  # noqa: E402
from mt5kit import risk as rk  # noqa: E402
from mt5kit import structure as st  # noqa: E402
from mt5kit.datasource import FixtureSource  # noqa: E402
from mt5kit.mcp_client import ForbiddenToolError, McpClient  # noqa: E402
from mt5kit.timeutil import ServerClock, trading_day_key  # noqa: E402

UTC = timezone.utc
DEMO_NOW = 1792491300  # มีแผน AUDUSD ผ่านทุกกฎในข้อมูลจำลอง seed 7


def bars_from(rows):
    return [ad.Bar(t=i * 3600.0, o=o, h=h, l=l, c=c) for i, (o, h, l, c) in enumerate(rows)]


class TestStructure(unittest.TestCase):
    def test_swing_confirm_needs_right_bars(self):
        rows = [(1, 1.1, .9, 1), (1, 1.2, .95, 1.1), (1.1, 1.5, 1, 1.4), (1.4, 1.3, 1.1, 1.2), (1.2, 1.25, 1.0, 1.1)]
        sw = st.find_swings(bars_from(rows), 2, 2)
        highs = [s for s in sw if s.kind == "high"]
        self.assertEqual(len(highs), 1)
        self.assertEqual(highs[0].idx, 2)
        self.assertEqual(highs[0].confirm_idx, 4)
        self.assertEqual(st.confirmed(sw, 3, "high"), [])

    def test_fvg_bull(self):
        rows = [(1, 1.01, .99, 1)] * 20 + [(1.0, 1.01, 0.99, 1.0), (1.0, 1.10, 1.0, 1.09), (1.09, 1.12, 1.05, 1.11)]
        b = bars_from(rows)
        atr = st.atr_wilder(b, 14)
        f = st.find_fvgs(b, atr, 0.1)
        self.assertTrue(any(x.direction == "bull" and abs(x.bottom - 1.01) < 1e-9 and abs(x.top - 1.05) < 1e-9 for x in f))

    def test_displacement_group_multi_bar(self):
        rows = [(1, 1.01, .99, 1)] * 20 + [(1.0, 1.012, .999, 1.011), (1.011, 1.024, 1.010, 1.023), (1.023, 1.036, 1.022, 1.035)]
        b = bars_from(rows)
        atr = st.atr_wilder(b, 14)
        cfg = {"max_bars": 3, "min_move_atr": 1.5, "min_body_ratio": 0.5, "close_outer_frac": 0.3}
        self.assertIsNotNone(st.displacement_group(b, atr, len(b) - 1, "long", cfg))
        self.assertIsNone(st.displacement_group(b, atr, len(b) - 1, "short", cfg))


class TestTime(unittest.TestCase):
    def test_native_mcp_server_time(self):
        from mt5kit.datasource import server_now_from_time_info
        payload = {"utc_time": "2026-11-09T02:08:08Z",
                   "trade_server_last_known_time": "2026-11-09T04:08:08"}
        server_now = server_now_from_time_info(payload)
        clock = ServerClock.from_profile({"mode": "auto"}, server_now,
                                         datetime(2026, 11, 9, 2, 8, 8, tzinfo=UTC))
        self.assertEqual(clock.to_utc(datetime(2026, 11, 9, 4, 8, 8)),
                         datetime(2026, 11, 9, 2, 8, 8, tzinfo=UTC))

    def test_ny_close_dst(self):
        c = ServerClock("ny_close")
        self.assertEqual(c.to_utc(datetime(2026, 7, 1, 0, 0)).isoformat(), "2026-06-30T21:00:00+00:00")
        self.assertEqual(c.to_utc(datetime(2026, 12, 1, 0, 0)).isoformat(), "2026-11-30T22:00:00+00:00")

    def test_trading_day_rollover(self):
        r = {"tz": "America/New_York", "time": "17:00"}
        t1 = datetime(2026, 11, 9, 21, 59, tzinfo=UTC).timestamp()  # 16:59 NY
        t2 = datetime(2026, 11, 9, 22, 1, tzinfo=UTC).timestamp()   # 17:01 NY
        self.assertEqual(trading_day_key(t1, r), "2026-11-09")
        self.assertEqual(trading_day_key(t2, r), "2026-11-10")

    def test_parse_formats(self):
        from mt5kit.timeutil import parse_server_time
        self.assertEqual(parse_server_time("2026.11.06 12:00:00.250"), datetime(2026, 11, 6, 12, 0))
        self.assertEqual(parse_server_time(86400), datetime(1970, 1, 2))


class TestGold(unittest.TestCase):
    def symbol(self):
        return ad.parse_symbol({"symbol": "GOLD#", "currency_base": "USD", "currency_profit": "USD",
                                "digits": 2, "point": 0.01, "tick_size": 0.01, "tick_value": 1.0,
                                "contract_size": 100, "volume_min": 0.01, "volume_step": 0.01,
                                "volume_max": 50, "bid": 2500, "ask": 2500.20}, ServerClock())

    def test_broker_alias_and_ambiguous_symbols(self):
        from mt5kit.engine import load_strategy, resolve_symbols
        aliases = load_strategy()["symbol_aliases"]
        result = resolve_symbols(["XAUUSD"], ["GOLD#"], {"symbol_suffix": "#"}, aliases)
        self.assertEqual(result["XAUUSD"]["name"], "GOLD#")
        ambiguous = resolve_symbols(["XAUUSD"], ["GOLD.a", "GOLD.b"], {}, aliases)
        self.assertFalse(ambiguous["XAUUSD"]["ok"])
        canonical = resolve_symbols(["XAUUSD"], ["XAUUSD#", "GOLD#"], {"symbol_suffix": "#"}, aliases)
        self.assertEqual(canonical["XAUUSD"]["name"], "XAUUSD#")

    def test_gold_contract_and_currency_exposure(self):
        sym = self.symbol()
        self.assertEqual((sym.base, sym.quote), ("XAU", "USD"))
        self.assertEqual(sym.missing, [])
        self.assertEqual(rk.money_per_lot(sym, 2.0), 200.0)
        exposure = rk.Exposure()
        exposure.add_ccy(sym.base, sym.quote, "long", 25.0)
        self.assertEqual(exposure.by_ccy["XAU"], {"long": 25.0, "short": 0.0})
        self.assertEqual(exposure.by_ccy["USD"], {"long": 0.0, "short": 25.0})

    def test_gold_shares_usd_currency_risk_cap(self):
        from mt5kit.engine import load_strategy
        acct = ad.Account("1", "s", "USD", 10000, 10000, 0, 10000, None, 100, "hedging", "demo")
        profile = json.loads((ROOT / "mt5kit/config/account-profile.example.json").read_text())
        exposure = rk.Exposure(total=50.0)
        exposure.add_ccy("EUR", "USD", "long", 25.0)
        exposure.add_ccy("GBP", "USD", "long", 25.0)
        sized = rk.size_position(acct, self.symbol(), "long", 2500, 2490, exposure,
                                 profile, 1.0, load_strategy())
        self.assertFalse(sized.ok)
        self.assertIn("เพดานความเสี่ยง", sized.reason)

    def test_usd_news_blocks_gold_but_eur_news_does_not(self):
        from mt5kit import news
        from mt5kit.engine import load_strategy
        cfg = load_strategy()["news"]
        usd = ad.NewsEvent(10000, "USD", "high", "CPI m/m", tier=1)
        eur = ad.NewsEvent(10000, "EUR", "high", "CPI m/m", tier=1)
        for name in ("XAUUSD", "GOLD#"):
            currencies = news.pair_currencies(name, "USD", "USD")
            self.assertEqual(currencies, ("XAU", "USD"))
            self.assertIs(news.blocked_now(10000, [usd], currencies, cfg), usd)
            self.assertIsNone(news.blocked_now(10000, [eur], currencies, cfg))


class TestRisk(unittest.TestCase):
    def sym(self):
        return ad.SymbolInfo("EURUSD", "EUR", "USD", "USD", 5, 1e-5, 1e-5, 1.0, 1.0, 100000, 0.01, 50, 0.01, 0, 0, 0,
                             1.0850, 1.0851, None, 0.0, 0.0, "points", True, "", "", None)

    def test_lot_example_from_plan(self):
        acct = ad.Account("1", "s", "USD", 10000, 10000, 0, 10000, None, 100, "hedging", "demo")
        prof = json.loads((ROOT / "mt5kit/config/account-profile.example.json").read_text())
        cfg = json.loads((ROOT / "mt5kit/config/strategy-v2.json").read_text())
        r = rk.size_position(acct, self.sym(), "long", 1.08450, 1.08200, rk.Exposure(), prof, 1.0, cfg)
        self.assertTrue(r.ok)
        self.assertAlmostEqual(r.loss_per_lot, 250.0, places=6)
        self.assertEqual(r.volume, 0.09)          # 25 / (250 + 7 + 2) = 0.0965 → ปัดลง 0.09
        self.assertLessEqual(r.risk_money, 25.0)

    def test_currency_cap_blocks_third_short_usd(self):
        acct = ad.Account("1", "s", "USD", 10000, 10000, 0, 10000, None, 100, "hedging", "demo")
        prof = json.loads((ROOT / "mt5kit/config/account-profile.example.json").read_text())
        cfg = json.loads((ROOT / "mt5kit/config/strategy-v2.json").read_text())
        ex = rk.Exposure()
        ex.add_ccy("EUR", "USD", "long", 25.0)
        ex.add_ccy("GBP", "USD", "long", 25.0)
        ex.total = 50.0
        s = self.sym(); s.name, s.base = "AUDUSD", "AUD"
        r = rk.size_position(acct, s, "long", 1.08450, 1.08200, ex, prof, 1.0, cfg)
        self.assertFalse(r.ok)

    def test_volume_below_min_skips(self):
        acct = ad.Account("1", "s", "USD", 100, 100, 0, 100, None, 100, "hedging", "demo")
        prof = json.loads((ROOT / "mt5kit/config/account-profile.example.json").read_text())
        cfg = json.loads((ROOT / "mt5kit/config/strategy-v2.json").read_text())
        r = rk.size_position(acct, self.sym(), "long", 1.08450, 1.08200, rk.Exposure(), prof, 1.0, cfg)
        self.assertFalse(r.ok)

    def test_zero_tick_value_blocked(self):
        s = self.sym(); s.tick_value = s.tick_value_loss = 0
        with self.assertRaises(rk.RiskDataError):
            rk.money_per_lot(s, 0.001)


class TestSafety(unittest.TestCase):
    def test_client_refuses_trade_tools(self):
        c = McpClient("http://127.0.0.1:1/mcp", {})
        for name in ("trade_send_pending_order", "trade_delete_order", "trade_modify_sl_tp", "trade_close_single_position"):
            with self.assertRaises(ForbiddenToolError):
                c.call_tool(name, {})

    def test_reads_codex_config(self):
        from mt5kit.mcp_client import load_server_config
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "mt5kit").mkdir()
            (Path(d) / ".codex").mkdir()
            (Path(d) / ".codex" / "config.toml").write_text(
                '[mcp_servers.terminal]\nurl = "http://127.0.0.1:22346/mcp"\nenabled = true\n'
                'http_headers = { "Authorization" = "Bearer abc123xyz" }\n')
            url, hdr = load_server_config("terminal", Path(d))
            self.assertEqual(url, "http://127.0.0.1:22346/mcp")
            self.assertEqual(hdr["Authorization"], "Bearer abc123xyz")

    def test_no_script_calls_trade_tools(self):
        for p in (ROOT / "mt5kit").glob("*.py"):
            src = p.read_text(encoding="utf-8")
            self.assertIsNone(re.search(r"call_tool\(\s*['\"]trade_", src), p.name)
            self.assertIsNone(re.search(r"\border_send\(", src), p.name)


def make_fixture(tmp: Path, now_ts: float, positions=None) -> Path:
    from fixturegen import build, write_fixture
    full = build(tmp / "x")
    fx = tmp / "fx"
    write_fixture(fx, full, datetime.fromtimestamp(now_ts, UTC), positions=positions)
    return fx


class TestEndToEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        cls.root = cls.tmp / "proj"
        cls.root.mkdir()
        cls.fx = make_fixture(cls.tmp, DEMO_NOW)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_scan_and_render(self):
        from mt5kit.engine import run_scan
        from mt5kit.render import render
        run_dir, rep = run_scan(self.root, FixtureSource(self.fx), "plan_only", "london", DEMO_NOW)
        self.assertEqual(rep["mode"], "plan_only")
        self.assertEqual(len(rep["scan"]), 11)
        self.assertIn("XAUUSD", {row["symbol"] for row in rep["scan"]})
        gold = next(row for row in rep["scan"] if row["symbol"] == "XAUUSD")
        self.assertFalse(gold["reason"].startswith(("BLOCKED", "Symbol")), gold["reason"])
        self.assertGreaterEqual(len(rep["plans"]), 1)
        p = rep["plans"][0]
        self.assertTrue(all(c["ok"] for c in p["checks"]))
        self.assertGreaterEqual(p["rr_net"], 1.5)
        self.assertLessEqual(p["risk_pct"], 0.0025 + 1e-9)
        html, issues = render(run_dir)
        self.assertEqual(issues, [])
        h = html.read_text(encoding="utf-8")
        self.assertIn("LightweightCharts", h)
        self.assertNotIn("Bearer", h)
        self.assertNotIn("51234567", h)  # เลขบัญชีเต็มต้องไม่อยู่ใน HTML

    def test_single_symbol_keeps_account_risk_and_ignores_other_management(self):
        from mt5kit.engine import run_scan
        from mt5kit.render import render

        class FocusSource(FixtureSource):
            requested = []

            def bars(self, symbol, tf, count):
                self.requested.append((symbol, tf))
                return super().bars(symbol, tf, count)

            def positions(self):
                return {"positions": [{"ticket": 881001, "symbol": "EURUSD", "type": "POSITION_TYPE_BUY",
                                       "volume": 0.01, "price_open": 1.1, "price_current": 1.1,
                                       "sl": 0.0, "tp": 1.2, "comment": "nsl existing"}],
                        "orders": [{"ticket": 881002, "symbol": "GBPUSD", "type": "ORDER_TYPE_BUY_LIMIT",
                                    "volume": 0.01, "price_open": 1.1, "sl": 1.0, "tp": 1.2,
                                    "comment": "nsl existing"}]}

        root = self.tmp / "single_risk"; root.mkdir(exist_ok=True)
        source = FocusSource(self.fx)
        run_dir, rep = run_scan(root, source, "plan_only", "london", DEMO_NOW, symbol="GOLD#")
        self.assertEqual(rep["selected_symbol"], "XAUUSD")
        self.assertEqual([row["symbol"] for row in rep["scan"]], ["XAUUSD"])
        self.assertEqual({symbol for symbol, tf in source.requested}, {"XAUUSD"})
        self.assertEqual({tf for symbol, tf in source.requested}, {"W1", "D1", "H4", "H1"})
        self.assertGreater(rep["risk"]["reserved_before"], 0)
        self.assertEqual(len(rep["risk"]["unknown"]), 1)
        self.assertTrue(any("คำนวณความเสี่ยงไม่ได้" in gate for gate in rep["gates"]))
        self.assertEqual(rep["plans"], [])
        self.assertEqual(len(rep["management"]), 2)
        self.assertTrue(all(row["action"] == "REPORT_ONLY" for row in rep["management"]))
        self.assertTrue(all(event["ccy"] == "USD" for event in rep["news"]))
        self.assertTrue(all(plan["std_symbol"] == "XAUUSD" for plan in rep["plans"]))
        self.assertIn("watch:XAUUSD", rep["charts"])
        self.assertTrue(rep["charts"]["watch:XAUUSD"]["tfs"]["H1"])
        html, issues = render(run_dir)
        self.assertEqual(issues, [])
        self.assertIn('"selected_symbol": "XAUUSD"', html.read_text())

    def test_single_reports_have_different_paths_at_same_time(self):
        from mt5kit.engine import run_scan
        root = self.tmp / "single_paths"; root.mkdir(exist_ok=True)
        gold, _ = run_scan(root, FixtureSource(self.fx), "plan_only", "london", DEMO_NOW, symbol="xauusd")
        euro, _ = run_scan(root, FixtureSource(self.fx), "plan_only", "london", DEMO_NOW, symbol="EURUSD")
        self.assertNotEqual(gold, euro)
        self.assertTrue(gold.name.endswith("-xauusd"))
        self.assertTrue(euro.name.endswith("-eurusd"))

    def test_invalid_single_symbol_is_rejected_before_reading_mt5(self):
        from mt5kit.engine import run_scan

        class NoReads:
            def __getattr__(self, name):
                raise AssertionError("Unexpected MT5 read: " + name)

        for symbol in ("BTCUSD", "", "../XAUUSD"):
            with self.subTest(symbol=symbol), self.assertRaises(ValueError):
                run_scan(self.tmp / "invalid", NoReads(), symbol=symbol)

    def test_analyze_command_generates_single_plan_only_report(self):
        import io
        from contextlib import redirect_stdout
        from unittest.mock import patch
        from mt5kit.cli import main
        root = self.tmp / "single_cli"; root.mkdir(exist_ok=True)
        profile = json.loads((ROOT / "mt5kit/config/account-profile.example.json").read_text())
        profile["execution"]["enabled"] = True
        (root / "account-profile.json").write_text(json.dumps(profile))
        from mt5kit.engine import load_strategy
        day = trading_day_key(DEMO_NOW, load_strategy()["sessions"]["trading_day_rollover"])
        macro_dir = root / "runs" / "macro"; macro_dir.mkdir(parents=True, exist_ok=True)
        (macro_dir / (day + ".json")).write_text(json.dumps({
            "currencies": {"XAU": "Gold", "USD": "Fed", "EUR": "ECB"},
            "pairs": {"XAUUSD": {"long": "neutral", "short": "neutral"}, "EURUSD": {"long": "neutral", "short": "neutral"}}
        }))
        output = io.StringIO()
        with patch("mt5kit.cli.find_project_root", return_value=root), redirect_stdout(output):
            code = main(["analyze", "XAUUSD", "--fixtures", str(self.fx), "--now",
                         datetime.fromtimestamp(DEMO_NOW, UTC).isoformat()])
        result = json.loads(output.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(result["mode"], "plan_only")
        self.assertEqual(result["selected_symbol"], "XAUUSD")
        self.assertEqual(len(result["scan"]), 1)
        self.assertTrue(Path(result["report_html"]).is_file())
        report = json.loads(Path(result["report_html"]).with_name("report.json").read_text())
        self.assertEqual(set(report["macro"]["currencies"]), {"XAU", "USD"})
        self.assertEqual(set(report["macro"]["pairs"]), {"XAUUSD"})

    def test_watch_explains_every_pair_without_plan(self):
        from mt5kit.engine import run_scan
        from mt5kit.render import render
        root = self.tmp / "proj_w"; root.mkdir(exist_ok=True)
        run_dir, rep = run_scan(root, FixtureSource(self.fx), "plan_only", "london", DEMO_NOW)
        planned = {p["std_symbol"] for p in rep["plans"]}
        watched = {w["symbol"] for w in rep["watch"]}
        scanned = {r["symbol"] for r in rep["scan"] if not r["reason"].startswith(("BLOCKED", "Symbol"))}
        self.assertEqual(watched | planned, scanned)
        for w in rep["watch"]:
            self.assertEqual(len(w["steps"]), 4)
            self.assertTrue(w["wait"])
            self.assertIn("watch:" + w["symbol"], rep["charts"])
            for lv in w["levels"]:
                self.assertIsInstance(lv["price"], float)
        (run_dir / "narrative.json").write_text(json.dumps({"overview": "x", "per_watch": {rep["watch"][0]["symbol"]: "ราคา 9.99999 ไม่มีจริง"}}))
        _, issues = render(run_dir)
        self.assertTrue(any("per_watch" in i for i in issues))

    def test_place_mode_falls_back_when_execution_disabled(self):
        from mt5kit.engine import run_scan
        root = self.tmp / "proj2"; root.mkdir(exist_ok=True)
        _, rep = run_scan(root, FixtureSource(self.fx), "analyze_and_place", "london", DEMO_NOW)
        self.assertEqual(rep["mode"], "plan_only")
        self.assertTrue(any("execution.enabled" in w for w in rep["warnings"]))

    def test_execution_flow_demo(self):
        from mt5kit.engine import run_scan
        from mt5kit.execution import ExecutionRefused, prepare_actions, reconcile, record_result
        root = self.tmp / "proj3"; root.mkdir(exist_ok=True)
        prof = json.loads((ROOT / "mt5kit/config/account-profile.example.json").read_text())
        _, rep = run_scan(root, FixtureSource(self.fx), "analyze_and_place", "london", DEMO_NOW)
        pid = rep["plans"][0]["plan_id"]
        with self.assertRaises(ExecutionRefused):
            prepare_actions(root, FixtureSource(self.fx), rep["run_id"], [pid], "user", now=DEMO_NOW + 60)
        prof["execution"]["enabled"] = True
        (root / "account-profile.json").write_text(json.dumps(prof))
        out = prepare_actions(root, FixtureSource(self.fx), rep["run_id"], [pid], "mode", now=DEMO_NOW + 60)
        self.assertEqual(out["refused"], [])
        a = out["actions"][0]
        self.assertEqual(a["tool"], "trade_send_pending_order")
        self.assertEqual(a["args"]["price"], rep["plans"][0]["entry"])
        self.assertTrue(a["args"]["comment"].startswith("nsl "))
        # เรียกแผนเดิมซ้ำ → ต้องถูกปฏิเสธหลังส่งแล้ว
        r = record_result(root, rep["run_id"], a["action_id"], {"retcode": 10009, "order": 777001})
        self.assertEqual(r["status"], "submitted")
        again = prepare_actions(root, FixtureSource(self.fx), rep["run_id"], [pid], "mode", now=DEMO_NOW + 90)
        self.assertEqual(again["actions"], [])
        # กระทบยอด: บัญชีจริงมี Pending ตาม comment
        p = rep["plans"][0]
        pos = {"positions": [], "orders": [{"ticket": 777001, "symbol": p["symbol"], "type": "ORDER_TYPE_SELL_LIMIT" if p["side"] == "short" else "ORDER_TYPE_BUY_LIMIT",
                                             "volume": a["args"]["volume"], "price_open": p["entry"], "sl": p["sl"], "tp": p["tp"],
                                             "time_expiration": a["args"]["expiration_time"], "magic": prof["execution"]["magic"], "comment": a["args"]["comment"]}]}
        (self.fx / "positions.json").write_text(json.dumps(pos))
        try:
            ex = reconcile(root, FixtureSource(self.fx), rep["run_id"])
        finally:
            (self.fx / "positions.json").write_text(json.dumps({"positions": [], "orders": []}))
        self.assertEqual(ex["status"], "success")
        self.assertEqual(ex["actions"][0]["ticket"], "777001")


if __name__ == "__main__":
    unittest.main()
