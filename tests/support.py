"""Synthetic broker fixtures and a local server. No IQ Option connection."""

import asyncio
import time
from copy import deepcopy
from pathlib import Path

from aiohttp import web

from nsl_iqoption.protocol import dumps, loads

FIXTURE = loads((Path(__file__).parent / "fixtures/initialization.json").read_text())


class FakeBroker:
    def __init__(self):
        self.login_status = 200
        self.login_response = {"code": "success", "ssid": "synthetic-session-token"}
        self.balances = [
            {"id": 10, "type": 4, "currency": "USD", "amount": "100.00"},
            {"id": 20, "type": 1, "currency": "USD", "amount": "50.00"},
        ]
        self.catalog = deepcopy(FIXTURE)
        self.order_action = "accept"
        self.orders = []
        self.history = []
        self.requests = []
        self.auth_ok = True
        self.object_acknowledgements = False
        self.user_id = 123
        self.reject_v4 = False
        self.reversed_catalog_window = False
        self.server_time_ms = None
        self.sockets = set()
        self.tasks = set()
        self.disconnects = 0

    async def start(self):
        app = web.Application()
        app.router.add_post("/login", self.login)
        app.router.add_post("/logout", self.logout)
        app.router.add_get("/ws", self.websocket)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        self.http_url = f"http://127.0.0.1:{port}"
        self.ws_url = f"ws://127.0.0.1:{port}/ws"

    async def close(self):
        for task in list(self.tasks):
            task.cancel()
        if self.tasks:
            await asyncio.gather(*self.tasks, return_exceptions=True)
        for ws in list(self.sockets):
            await ws.close()
        await self.runner.cleanup()

    async def login(self, request):
        await request.json()
        return web.Response(
            text=dumps(self.login_response),
            status=self.login_status,
            content_type="application/json",
        )

    async def logout(self, request):
        return web.json_response({"success": True})

    async def reply(self, ws, name, msg, request_id=""):
        await ws.send_str(dumps({"name": name, "msg": msg, "request_id": request_id}))

    def position(self, order, broker_id="10001", *, closed=True):
        return {
            "external_id": int(broker_id),
            "user_balance_id": order["msg"]["body"]["user_balance_id"],
            "instrument_type": "turbo-option"
            if order["msg"]["body"]["option_type_id"] == 3
            else "binary-option",
            "status": "closed" if closed else "open",
            "request_id": order["request_id"],
            "close_profit": "1.85",
            "pnl": "0.85",
        }

    async def delayed_ack(self, ws, request_id):
        await asyncio.sleep(0.15)
        if not ws.closed:
            await self.reply(ws, "option", {"id": 10001}, request_id)

    async def websocket(self, request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        self.sockets.add(ws)
        try:
            async for frame in ws:
                envelope = loads(frame.data)
                self.requests.append(envelope)
                rid = envelope.get("request_id", "")
                outer = envelope["name"]
                if outer in ("authenticate", "ssid"):
                    await self.reply(ws, "authenticated", self.auth_ok, rid)
                    await self.reply(ws, "timeSync", self.server_time_ms or int(time.time() * 1000))
                elif outer == "subscribeMessage":
                    await self.reply(
                        ws,
                        "result",
                        {"success": True} if self.object_acknowledgements else True,
                        rid,
                    )
                elif outer == "sendMessage":
                    name = envelope["msg"]["name"]
                    body = envelope["msg"].get("body", {})
                    if self.object_acknowledgements:
                        await self.reply(ws, "result", {"success": True}, rid)
                    if name == "core.get-profile":
                        await self.reply(
                            ws, "profile", {"result": {"user_id": self.user_id, "group_id": 1}}, rid
                        )
                    elif name == "get-balances":
                        await self.reply(ws, "balances", self.balances, rid)
                    elif name == "get-initialization-data":
                        if self.reject_v4 and envelope["msg"]["version"] == "4.0":
                            await self.reply(
                                ws,
                                "result",
                                {"success": False, "message": "version not supported"},
                                rid,
                            )
                        else:
                            catalog = deepcopy(self.catalog)
                            if self.reversed_catalog_window:
                                catalog["binary"]["actives"]["76"]["schedule"].append(
                                    [2_000_000_300, 2_000_000_200]
                                )
                            await self.reply(ws, "initialization-data", catalog, rid)
                    elif name == "trading-settings.get-trading-group-params":
                        await self.reply(
                            ws,
                            "trading-group-params",
                            {"commissions": [{"active_id": 76, "value": 15}]},
                            rid,
                        )
                    elif name == "portfolio.get-history-positions":
                        rows = [
                            p
                            for p in self.history
                            if str(p["user_balance_id"]) == str(body["user_balance_id"])
                        ]
                        await self.reply(
                            ws,
                            "history-positions",
                            {"positions": rows[body["offset"] : body["offset"] + body["limit"]]},
                            rid,
                        )
                    elif name == "binary-options.open-option":
                        self.orders.append(envelope)
                        if self.order_action == "close":
                            await ws.close()
                        elif self.order_action == "reject":
                            await self.reply(
                                ws,
                                "result",
                                {
                                    "success": False,
                                    "message": "invalid expiration synthetic-password synthetic-session-token",
                                },
                                rid,
                            )
                        elif self.order_action == "reject_with_id":
                            await self.reply(
                                ws,
                                "option",
                                {
                                    "id": 10001,
                                    "isSuccessful": False,
                                    "message": "invalid expiration",
                                },
                                rid,
                            )
                        elif self.order_action == "timeout":
                            await self.reply(ws, "result", True, rid)
                        elif self.order_action == "malformed_ack":
                            await self.reply(ws, "option", {"id": "bad-identifier"}, rid)
                        elif self.order_action == "settle_first":
                            await self.reply(ws, "position-changed", self.position(envelope), "")
                            await self.reply(ws, "option", {"id": 10001}, rid)
                        elif self.order_action == "late":
                            task = asyncio.create_task(self.delayed_ack(ws, rid))
                            self.tasks.add(task)
                            task.add_done_callback(self.tasks.discard)
                        else:
                            await self.reply(ws, "result", True, rid)
                            await self.reply(ws, "option", {"id": 10001}, rid)
        finally:
            self.sockets.discard(ws)
            self.disconnects += 1
        return ws
