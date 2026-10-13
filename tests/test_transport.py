import asyncio
import unittest
from decimal import Decimal

from nsl_iqoption.errors import BrokerError, RequestError
from nsl_iqoption.protocol import loads, message
from nsl_iqoption.transport import WebSocketTransport


class InstantSocket:
    closed = False

    def __init__(self, transport):
        self.transport = transport
        self.wires = []
        self.reply = True

    async def send_str(self, wire):
        self.wires.append(wire)
        envelope = loads(wire)
        if self.reply:
            self.transport.dispatch(
                {"name": "option", "request_id": envelope["request_id"], "msg": {"id": 99}}
            )


class TransportTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.transport = WebSocketTransport(request_timeout=0.03)
        self.socket = InstantSocket(self.transport)
        self.transport.ws = self.socket

    async def test_handler_registered_before_immediate_reply(self):
        result = await self.transport.request(
            message("test", "1.0", {"price": Decimal("1.01")}), expected={"option"}
        )
        self.assertEqual(result, {"id": 99})
        self.assertEqual(self.transport._pending, {})

    async def test_disconnected_before_send_is_provable(self):
        self.socket.closed = True
        with self.assertRaises(RequestError) as error:
            await self.transport.request(message("test", "1.0"), expected={"option"})
        self.assertFalse(error.exception.may_have_sent)
        self.assertEqual(self.socket.wires, [])

    async def test_timeout_after_send_is_ambiguous(self):
        self.socket.reply = False
        with self.assertRaises(RequestError) as error:
            await self.transport.request(message("test", "1.0"), expected={"option"})
        self.assertTrue(error.exception.may_have_sent)
        self.assertEqual(len(self.socket.wires), 1)
        self.assertEqual(self.transport._pending, {})

    async def test_out_of_order_replies_and_unsolicited_events(self):
        self.socket.reply = False
        events = []
        self.transport.on_event = events.append
        first = asyncio.create_task(
            self.transport.request(message("a", "1.0"), expected={"option"}, request_id="a")
        )
        second = asyncio.create_task(
            self.transport.request(message("b", "1.0"), expected={"option"}, request_id="b")
        )
        await asyncio.sleep(0)
        self.transport.dispatch({"name": "option", "request_id": "b", "msg": {"id": 2}})
        self.transport.dispatch({"name": "timeSync", "msg": 2_000_000_000_000})
        self.transport.dispatch({"name": "option", "request_id": "a", "msg": {"id": 1}})
        self.assertEqual(await first, {"id": 1})
        self.assertEqual(await second, {"id": 2})
        self.assertEqual(len(events), 3)

    async def test_generic_success_does_not_accept_data_request(self):
        self.socket.reply = False
        task = asyncio.create_task(
            self.transport.request(
                message("a", "1.0"), expected={"option", "result"}, request_id="a"
            )
        )
        await asyncio.sleep(0)
        self.transport.dispatch({"name": "result", "request_id": "a", "msg": True})
        self.assertFalse(task.done())
        self.transport.dispatch({"name": "option", "request_id": "a", "msg": {"id": 1}})
        self.assertEqual(await task, {"id": 1})

    async def test_object_acknowledgement_waits_for_profile_data(self):
        self.socket.reply = False
        task = asyncio.create_task(
            self.transport.request(
                message("core.get-profile", "1.0"),
                expected={"profile", "result"},
                request_id="profile-request",
            )
        )
        await asyncio.sleep(0)
        self.transport.dispatch(
            {"name": "result", "request_id": "profile-request", "msg": {"success": True}}
        )
        await asyncio.sleep(0)
        self.assertFalse(task.done())
        profile = {"isSuccessful": True, "result": {"id": 123}}
        self.transport.dispatch(
            {"name": "profile", "request_id": "profile-request", "msg": profile}
        )
        self.assertEqual(await task, profile)

    async def test_object_acknowledgement_is_valid_for_subscription(self):
        self.socket.reply = False
        task = asyncio.create_task(
            self.transport.request(
                message("subscription", "1.0"),
                expected={"result"},
                request_id="subscription-request",
            )
        )
        await asyncio.sleep(0)
        self.transport.dispatch(
            {"name": "result", "request_id": "subscription-request", "msg": {"success": True}}
        )
        self.assertEqual(await task, {"success": True})

    async def test_object_acknowledgement_cannot_accept_order(self):
        self.socket.reply = False
        task = asyncio.create_task(
            self.transport.request(
                message("binary-options.open-option", "1.0"),
                expected={"option", "result"},
                request_id="order-request",
            )
        )
        await asyncio.sleep(0)
        self.transport.dispatch(
            {"name": "result", "request_id": "order-request", "msg": {"success": True}}
        )
        with self.assertRaises(RequestError):
            await task

    async def test_cancelled_request_removes_pending_handler(self):
        self.socket.reply = False
        task = asyncio.create_task(self.transport.request(message("a", "1.0"), expected={"option"}))
        await asyncio.sleep(0)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(self.transport._pending, {})

    async def test_unverified_numeric_status_does_not_prove_rejection(self):
        self.socket.reply = False
        task = asyncio.create_task(
            self.transport.request(message("a", "1.0"), expected={"option"}, request_id="a")
        )
        await asyncio.sleep(0)
        self.transport.dispatch(
            {"name": "option", "request_id": "a", "status": 2000, "msg": {"id": 1}}
        )
        self.assertEqual(await task, {"id": 1})

    async def test_explicit_error_is_sanitized(self):
        self.socket.reply = False
        task = asyncio.create_task(
            self.transport.request(message("a", "1.0"), expected={"option"}, request_id="a")
        )
        await asyncio.sleep(0)
        self.transport.dispatch(
            {
                "name": "result",
                "request_id": "a",
                "msg": {"success": False, "message": "raw password=private token=secret"},
            }
        )
        with self.assertRaises(BrokerError) as error:
            await task
        self.assertNotIn("private", str(error.exception))
