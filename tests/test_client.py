import asyncio
import tempfile
import unittest
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

from support import FakeBroker

from nsl_iqoption.auth import AuthClient
from nsl_iqoption.client import IQOptionClient
from nsl_iqoption.errors import BrokerError
from nsl_iqoption.models import AuthChallenge, OrderInput, OrderState, Product, SessionState
from nsl_iqoption.transport import WebSocketTransport


class ClientTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.broker = FakeBroker()
        self.broker.server_time_ms = 2_000_000_000_000
        await self.broker.start()
        self.client = IQOptionClient(
            Path(self.directory.name) / "orders.sqlite3",
            auth=AuthClient(
                login_url=self.broker.http_url + "/login",
                logout_url=self.broker.http_url + "/logout",
            ),
            transport=WebSocketTransport(
                url=self.broker.ws_url, request_timeout=0.5, connect_timeout=1
            ),
        )

    async def asyncTearDown(self):
        await self.client.close()
        await self.broker.close()
        self.directory.cleanup()

    async def login(self):
        return await self.client.login("synthetic@example.invalid", "synthetic-password")

    async def prepare(self, amount=Decimal("1.00"), **kwargs):
        return await self.client.prepare_order(
            OrderInput("EURUSD-OTC", Product.TURBO, "call", amount, 1), **kwargs
        )

    async def test_login_requires_profile_and_balances(self):
        session = await self.login()
        self.assertEqual(session.user_id, "123")
        self.assertEqual(session.balance_id, "10")
        self.assertEqual(self.client.state, SessionState.CONNECTED)
        self.assertNotIn("synthetic-session-token", repr(self.client.auth))

    async def test_login_and_catalog_ignore_object_acknowledgements(self):
        self.broker.object_acknowledgements = True
        self.broker.reversed_catalog_window = True
        session = await self.login()
        self.assertEqual(session.user_id, "123")
        self.assertEqual(session.balance_id, "10")
        self.assertEqual(self.client.state, SessionState.CONNECTED)
        catalog = await self.client.list_symbols(refresh=True)
        self.assertEqual(len(catalog.symbols), 3)
        self.assertEqual(self.broker.orders, [])

    async def test_order_acceptance_requires_data_after_object_acknowledgement(self):
        self.broker.object_acknowledgements = True
        await self.login()
        order = await self.prepare()
        accepted = await self.client.submit_order(order.local_id)
        self.assertEqual(accepted.state, OrderState.ACCEPTED)
        self.assertEqual(accepted.broker_id, "10001")
        self.assertEqual(len(self.broker.orders), 1)

    async def test_http_success_without_token_is_not_login(self):
        self.broker.login_response = {"success": True}
        with self.assertRaises(BrokerError) as error:
            await self.login()
        self.assertEqual(error.exception.code, "PROTOCOL_UNSUPPORTED")
        self.assertIsNone(self.client.auth.session)

    async def test_invalid_credentials_error_contains_no_secret(self):
        self.broker.login_status = 401
        self.broker.login_response = {
            "success": False,
            "message": "synthetic-password synthetic-session-token",
        }
        with self.assertRaises(BrokerError) as error:
            await self.login()
        self.assertEqual(error.exception.code, "INVALID_CREDENTIALS")
        self.assertNotIn("synthetic-password", str(error.exception))

    async def test_challenge_is_exposed_without_claiming_login(self):
        self.broker.login_response = {
            "code": "verification_required",
            "data": {"token": "private-challenge-token"},
        }
        result = await self.login()
        self.assertIsInstance(result, AuthChallenge)
        self.assertFalse(result.supported)
        self.assertEqual(self.client.state, SessionState.CHALLENGE_REQUIRED)
        self.assertNotIn("private-challenge-token", repr(result))
        with self.assertRaises(BrokerError):
            await self.client.complete_challenge(result.challenge_id, "123456")

    async def test_websocket_rejected_session_is_not_login(self):
        self.broker.auth_ok = False
        with self.assertRaises(BrokerError) as error:
            await self.login()
        self.assertEqual(error.exception.code, "SESSION_EXPIRED")
        self.assertFalse(self.client.transport.connected)

    async def test_invalid_profile_is_not_login(self):
        self.broker.user_id = "not-a-user-id"
        with self.assertRaises(BrokerError):
            await self.login()
        self.assertEqual(self.client.state, SessionState.ERROR)

    async def test_rate_limit_is_separate(self):
        self.broker.login_status = 429
        with self.assertRaises(BrokerError) as error:
            await self.login()
        self.assertEqual(error.exception.code, "RATE_LIMITED")

    async def test_missing_practice_never_selects_real(self):
        self.broker.balances = [self.broker.balances[1]]
        session = await self.login()
        self.assertIsNone(session.balance_id)
        with self.assertRaises(BrokerError):
            await self.prepare()
        with self.assertRaises(BrokerError):
            self.client.select_balance("20")
        self.client.select_balance("20", enable_real=True)
        self.assertEqual((await self.prepare()).mode, "real")

    async def test_catalog_search_otc_and_product_filter(self):
        await self.login()
        catalog = await self.client.list_symbols(search="EURUSD", open_only=True, otc=True)
        self.assertEqual(len(catalog.symbols), 2)
        self.assertEqual({s.product for s in catalog.symbols}, {Product.BINARY, Product.TURBO})
        self.assertEqual(catalog.symbols[0].payout_percent, Decimal(85))

    async def test_initialization_read_only_version_fallback(self):
        self.broker.reject_v4 = True
        await self.login()
        await self.client.list_symbols()
        self.assertEqual(self.client.protocol_versions["initialization"], "3.0")
        requests = [
            r
            for r in self.broker.requests
            if isinstance(r.get("msg"), dict) and r["msg"].get("name") == "get-initialization-data"
        ]
        self.assertEqual([r["msg"]["version"] for r in requests], ["4.0", "3.0"])

    async def test_closed_symbol_blocks_order(self):
        await self.login()
        with self.assertRaises(BrokerError) as error:
            await self.client.prepare_order(
                OrderInput("EURUSD", Product.TURBO, "call", Decimal(1), 1)
            )
        self.assertEqual(error.exception.code, "SYMBOL_UNAVAILABLE")
        self.assertEqual(self.broker.orders, [])

    async def test_amount_validation_rejects_bad_values(self):
        await self.login()
        for amount in (
            Decimal(0),
            Decimal(-1),
            Decimal("NaN"),
            Decimal("Infinity"),
            Decimal("1.001"),
            Decimal("0.50"),
            Decimal(101),
        ):
            with self.subTest(amount=str(amount)), self.assertRaises(BrokerError):
                await self.prepare(amount)
        self.assertEqual(self.broker.orders, [])

    async def test_prepare_is_not_a_submission(self):
        await self.login()
        order = await self.prepare()
        self.assertEqual(order.state, OrderState.PREPARED)
        self.assertEqual(self.broker.orders, [])

    async def test_submission_is_one_shot_even_concurrently(self):
        await self.login()
        order = await self.prepare()
        results = await asyncio.gather(
            self.client.submit_order(order.local_id),
            self.client.submit_order(order.local_id),
            return_exceptions=True,
        )
        self.assertEqual(len(self.broker.orders), 1)
        self.assertEqual(sum(isinstance(result, BrokerError) for result in results), 1)
        sent = self.broker.orders[0]["msg"]["body"]
        self.assertEqual(sent["price"], Decimal("1.00"))
        self.assertEqual(sent["expired"], order.expiration)
        self.assertEqual(sent["user_balance_id"], 10)

    async def test_balance_change_does_not_redirect_intent(self):
        await self.login()
        order = await self.prepare()
        self.client.select_balance("20", enable_real=True)
        accepted = await self.client.submit_order(order.local_id)
        self.assertEqual(accepted.balance_id, "10")
        self.assertEqual(self.broker.orders[0]["msg"]["body"]["user_balance_id"], 10)

    async def test_generic_success_without_option_id_is_unknown(self):
        await self.login()
        order = await self.prepare()
        self.broker.order_action = "timeout"
        self.client.transport.request_timeout = 0.05
        result = await self.client.submit_order(order.local_id)
        self.assertEqual(result.state, OrderState.UNKNOWN)
        self.assertEqual(len(self.broker.orders), 1)
        with self.assertRaises(BrokerError):
            await self.client.submit_order(order.local_id)

    async def test_malformed_ack_is_unknown_not_rejected(self):
        await self.login()
        order = await self.prepare()
        self.broker.order_action = "malformed_ack"
        result = await self.client.submit_order(order.local_id)
        self.assertEqual(result.state, OrderState.UNKNOWN)

    async def test_broker_rejection_is_recorded_and_sanitized(self):
        await self.login()
        order = await self.prepare()
        self.broker.order_action = "reject"
        rejected = await self.client.submit_order(order.local_id)
        self.assertEqual(rejected.state, OrderState.REJECTED)
        self.assertEqual(rejected.error_code, "INVALID_EXPIRATION")
        self.assertNotIn("synthetic-password", repr(rejected))

    async def test_disconnect_after_send_does_not_retry(self):
        await self.login()
        order = await self.prepare()
        self.broker.order_action = "close"
        result = await self.client.submit_order(order.local_id)
        self.assertEqual(result.state, OrderState.UNKNOWN)
        self.assertEqual(len(self.broker.orders), 1)
        self.assertEqual(self.client.state, SessionState.RECONNECTING)

    async def test_failed_ack_with_identifier_cannot_be_accepted(self):
        await self.login()
        order = await self.prepare()
        self.broker.order_action = "reject_with_id"
        result = await self.client.submit_order(order.local_id)
        self.assertEqual(result.state, OrderState.REJECTED)
        self.assertIsNone(result.broker_id)

    async def test_settlement_before_ack_is_not_regressed(self):
        await self.login()
        order = await self.prepare()
        self.broker.order_action = "settle_first"
        result = await self.client.submit_order(order.local_id)
        self.assertEqual(result.state, OrderState.SETTLED)
        self.assertEqual(result.net_profit, Decimal("0.85"))

    async def test_late_ack_resolves_unknown_without_resend(self):
        await self.login()
        order = await self.prepare()
        self.broker.order_action = "late"
        self.client.transport.request_timeout = 0.05
        result = await self.client.submit_order(order.local_id)
        self.assertEqual(result.state, OrderState.UNKNOWN)
        await asyncio.sleep(0.2)
        result = await self.client.get_order(order.local_id, reconcile=False)
        self.assertEqual(result.state, OrderState.ACCEPTED)
        self.assertEqual(len(self.broker.orders), 1)

    async def test_unknown_orders_require_explicit_acknowledgement(self):
        await self.login()
        order = await self.prepare()
        self.client.journal.save(replace(order, state=OrderState.UNKNOWN))
        with self.assertRaises(BrokerError) as error:
            await self.prepare()
        self.assertEqual(error.exception.code, "ORDER_STATUS_UNKNOWN")
        self.assertEqual((await self.prepare(acknowledge_unknown=True)).state, OrderState.PREPARED)

    async def test_history_pagination_and_profit_fields(self):
        await self.login()
        order = await self.prepare()
        accepted = await self.client.submit_order(order.local_id)
        position = self.broker.position(self.broker.orders[0])
        self.broker.history = [
            dict(position, external_id=20000 + i, request_id="other") for i in range(100)
        ] + [position]
        settled = await self.client.get_order(accepted.local_id)
        self.assertEqual(settled.state, OrderState.SETTLED)
        self.assertEqual(settled.gross_return, Decimal("1.85"))
        self.assertEqual(settled.net_profit, Decimal("0.85"))
        offsets = [
            r["msg"]["body"]["offset"]
            for r in self.broker.requests
            if isinstance(r.get("msg"), dict)
            and r["msg"].get("name") == "portfolio.get-history-positions"
        ]
        self.assertEqual(offsets, [0, 100])

    async def test_unknown_cannot_be_guessed_from_similar_trade(self):
        await self.login()
        order = await self.prepare()
        self.broker.order_action = "timeout"
        self.client.transport.request_timeout = 0.05
        unknown = await self.client.submit_order(order.local_id)
        self.broker.history = [
            dict(self.broker.position(self.broker.orders[0]), request_id="different-request")
        ]
        self.assertEqual((await self.client.get_order(unknown.local_id)).state, OrderState.UNKNOWN)

    async def test_unknown_resolves_only_with_exact_correlation(self):
        await self.login()
        order = await self.prepare()
        self.broker.order_action = "timeout"
        self.client.transport.request_timeout = 0.05
        unknown = await self.client.submit_order(order.local_id)
        self.broker.history = [self.broker.position(self.broker.orders[0])]
        self.assertEqual((await self.client.get_order(unknown.local_id)).state, OrderState.SETTLED)

    async def test_reconnect_refreshes_without_sending_order(self):
        await self.login()
        await self.client.list_symbols()
        self.broker.auth_ok = False
        for socket in list(self.broker.sockets):
            await socket.close()
        for _ in range(60):
            if self.client.state is SessionState.SESSION_EXPIRED:
                break
            await asyncio.sleep(0.05)
        self.assertEqual(self.client.state, SessionState.SESSION_EXPIRED)
        self.assertEqual(self.broker.orders, [])

    async def test_logout_clears_secrets_and_disables_trading(self):
        await self.login()
        self.client.select_balance("20", enable_real=True)
        await self.client.logout()
        self.assertIsNone(self.client.auth.session)
        self.assertEqual(self.client._real_enabled, set())
        with self.assertRaises(BrokerError):
            await self.prepare()

    async def test_canceled_submission_preserves_unknown_intent(self):
        await self.login()
        order = await self.prepare()
        self.broker.order_action = "timeout"
        task = asyncio.create_task(self.client.submit_order(order.local_id))
        for _ in range(50):
            if self.broker.orders:
                break
            await asyncio.sleep(0.002)
        self.assertEqual(len(self.broker.orders), 1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        recovered = self.client.journal.get(order.local_id)
        self.assertEqual(recovered.state, OrderState.UNKNOWN)
        with self.assertRaises(BrokerError):
            await self.client.submit_order(order.local_id)
