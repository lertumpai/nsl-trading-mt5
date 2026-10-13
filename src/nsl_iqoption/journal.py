"""Durable one-shot order intents, including interrupted submissions."""

import sqlite3
from dataclasses import asdict, replace
from decimal import Decimal
from pathlib import Path

from .errors import BrokerError
from .models import Order, OrderState, Product
from .protocol import dumps, loads


class OrderJournal:
    def __init__(self, path: str | Path = ".runtime/orders.sqlite3"):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=10)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS orders (local_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, state TEXT NOT NULL, request_id TEXT UNIQUE, broker_id TEXT, payload TEXT NOT NULL)"
        )
        # A process may have died at any point between durable intent and receipt.
        with self.db:
            rows = self.db.execute(
                "SELECT payload FROM orders WHERE state=?", (OrderState.SUBMITTING,)
            ).fetchall()
            for (payload,) in rows:
                self.save(
                    replace(
                        self._decode(payload),
                        state=OrderState.UNKNOWN,
                        error_code="ORDER_STATUS_UNKNOWN",
                    )
                )

    @staticmethod
    def _decode(payload: str) -> Order:
        data = loads(payload)
        data["product"] = Product(data["product"])
        data["state"] = OrderState(data["state"])
        for key in ("amount", "gross_return", "net_profit"):
            if data[key] is not None:
                data[key] = Decimal(str(data[key]))
        return Order(**data)

    def save(self, order: Order) -> Order:
        with self.db:
            self.db.execute(
                "INSERT INTO orders VALUES (?,?,?,?,?,?) ON CONFLICT(local_id) DO UPDATE SET state=excluded.state, request_id=excluded.request_id, broker_id=excluded.broker_id, payload=excluded.payload",
                (
                    order.local_id,
                    order.user_id,
                    order.state,
                    order.request_id,
                    order.broker_id,
                    dumps(asdict(order)),
                ),
            )
        return order

    def get(self, local_id: str) -> Order:
        row = self.db.execute("SELECT payload FROM orders WHERE local_id=?", (local_id,)).fetchone()
        if row is None:
            raise BrokerError("ORDER_NOT_FOUND", "No order exists with this local ID.")
        return self._decode(row[0])

    def list(self, user_id: str | None = None) -> list[Order]:
        query = "SELECT payload FROM orders"
        args = ()
        if user_id is not None:
            query += " WHERE user_id=?"
            args = (user_id,)
        return [
            self._decode(row[0]) for row in self.db.execute(query + " ORDER BY rowid DESC", args)
        ]

    def claim(self, local_id: str, request_id: str, sent_at: float) -> Order:
        """Compare-and-set under a write lock prevents competing process sends."""
        try:
            self.db.execute("BEGIN IMMEDIATE")
            order = self.get(local_id)
            if order.state is not OrderState.PREPARED:
                raise BrokerError(
                    "ORDER_ALREADY_SUBMITTED", "This intent cannot be submitted again."
                )
            claimed = replace(
                order, state=OrderState.SUBMITTING, request_id=request_id, sent_at=sent_at
            )
            self.db.execute(
                "UPDATE orders SET state=?, request_id=?, payload=? WHERE local_id=? AND state=?",
                (claimed.state, request_id, dumps(asdict(claimed)), local_id, OrderState.PREPARED),
            )
            self.db.commit()
            return claimed
        except BaseException:
            self.db.rollback()
            raise

    def close(self) -> None:
        self.db.close()
