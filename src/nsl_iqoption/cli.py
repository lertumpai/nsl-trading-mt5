"""One interactive session; passwords are prompted, never command arguments."""

import argparse
import asyncio
import getpass
import logging
import shlex
import sys
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from .client import IQOptionClient
from .errors import BrokerError
from .models import AuthChallenge, Order, OrderInput, Product
from .transport import WebSocketTransport

HELP = """Commands:
  login                         Prompt for identifier/password
  session                       Connection state and selected balance
  balances                      Refresh available balances
  balance ID [--real]            Explicitly select practice/real balance
  symbols [SEARCH] [--open] [--otc|--no-otc] [--refresh]
  prepare SYMBOL binary|turbo call|put AMOUNT MINUTES [--ack-unknown]
                                Save an intent and display exact expiration
  submit LOCAL_ID [--ack-unknown]
                                Review intent and type 'submit' to send once
  trade SYMBOL binary|turbo call|put AMOUNT MINUTES [--ack-unknown]
                                Prepare, review, and submit in one workflow
  orders                        Local journal for the logged-in account
  order LOCAL_ID [--local]       Query broker history or show local state
  logout                        Close and invalidate the account session
  help                          Show these commands
  quit                          Close session and exit

MINUTES is a nominal duration; the exact broker-aligned expiry is shown
before submission. Binary and turbo availability are separate.
"""


async def prompt(text: str, *, secret: bool = False) -> str:
    return await asyncio.to_thread(getpass.getpass if secret else input, text)


def timestamp(value: float) -> str:
    return datetime.fromtimestamp(value, UTC).astimezone().isoformat(timespec="seconds")


def show_order(order: Order) -> None:
    print(f"{order.local_id}  {order.state}  broker={order.broker_id or 'unknown'}")
    print(
        f"  {order.mode.upper()} balance={order.balance_id} | {order.symbol} ({order.active_id}) {order.product} {order.direction.upper()}"
    )
    print(f"  Stake {order.amount} {order.currency} | expires {timestamp(order.expiration)}")
    if order.gross_return is not None or order.net_profit is not None:
        print(
            f"  Gross return={order.gross_return if order.gross_return is not None else 'unknown'} | net P/L={order.net_profit if order.net_profit is not None else 'unknown'} {order.currency}"
        )
    if order.error_code:
        print(f"  {order.error_code}")


def parse_order(args: list[str]) -> OrderInput:
    if len(args) != 5:
        raise BrokerError("INVALID_INPUT", "Expected SYMBOL PRODUCT DIRECTION AMOUNT MINUTES.")
    try:
        return OrderInput(
            args[0], Product(args[1].lower()), args[2], Decimal(args[3]), int(args[4])
        )
    except (ValueError, InvalidOperation):
        raise BrokerError(
            "INVALID_INPUT", "Use binary/turbo, a decimal amount, and integer minutes."
        ) from None


async def confirm_submit(client: IQOptionClient, local_id: str, ack_unknown: bool) -> None:
    order = await client.get_order(local_id, reconcile=False)
    show_order(order)
    if await prompt("Type submit to place this order, or press Enter to cancel: ") != "submit":
        print("No order submitted. Intent remains prepared.")
        return
    show_order(await client.submit_order(local_id, acknowledge_unknown=ack_unknown))


async def command(client: IQOptionClient, words: list[str]) -> bool:
    name, *args = words
    if name in ("quit", "exit"):
        return False
    if name == "help":
        print(HELP)
    elif name == "login":
        if args:
            raise BrokerError(
                "INVALID_INPUT", "Login takes no arguments; credentials are prompted privately."
            )
        result = await client.login(
            await prompt("IQ Option identifier: "), await prompt("Password: ", secret=True)
        )
        if isinstance(result, AuthChallenge):
            print(f"{client.state}: {result.message}")
        else:
            print(
                f"Login authenticated. User {result.user_id}; practice balance {result.balance_id or 'unavailable'}."
            )
            unresolved = [o for o in client.list_orders() if o.state in ("UNKNOWN", "SUBMITTING")]
            if unresolved:
                print(
                    f"{len(unresolved)} unresolved order(s) recovered. Use orders/order to inspect them."
                )
    elif name == "session":
        print(
            f"{client.state} | user={client.user_id or 'none'} | balance={client.selected_balance_id or 'none'}"
        )
    elif name == "balances":
        for balance in await client.list_balances():
            mark = "*" if balance.id == client.selected_balance_id else " "
            print(f"{mark} {balance.id:>12} {balance.mode:>11} {balance.amount} {balance.currency}")
    elif name == "balance":
        if len(args) not in (1, 2) or (len(args) == 2 and args[1] != "--real"):
            raise BrokerError("INVALID_INPUT", "Use balance ID [--real].")
        await client.list_balances()
        balance = client.select_balance(args[0], enable_real="--real" in args)
        print(
            f"Selected {balance.mode.upper()} balance {balance.id}: {balance.amount} {balance.currency}"
        )
    elif name == "symbols":
        allowed = {"--open", "--otc", "--no-otc", "--refresh"}
        if any(a.startswith("--") and a not in allowed for a in args) or (
            "--otc" in args and "--no-otc" in args
        ):
            raise BrokerError("INVALID_INPUT", "Unrecognized or conflicting symbol filters.")
        catalog = await client.list_symbols(
            search=" ".join(a for a in args if not a.startswith("--")),
            open_only="--open" in args,
            otc=True if "--otc" in args else False if "--no-otc" in args else None,
            refresh="--refresh" in args,
        )
        try:
            now = client.transport.clock.now()
        except BrokerError:
            now = None
        print(
            f"Catalog refreshed {timestamp(catalog.refreshed_at)} | stale={catalog.stale} | {len(catalog.symbols)} products"
        )
        print(
            f"{'SYMBOL':24} {'ID':>8} {'PRODUCT':8} {'STATUS':8} {'PAYOUT':8} EXPIRATIONS (minutes)"
        )
        for symbol in catalog.symbols:
            payout = f"{symbol.payout_percent}%" if symbol.payout_percent is not None else "unknown"
            durations = ",".join(str(n // 60) for n in symbol.expiration_seconds) or "unknown"
            print(
                f"{symbol.symbol:24} {symbol.active_id:>8} {symbol.product:8} {symbol.market_status(now):8} {payout:8} {durations}"
            )
    elif name in ("prepare", "trade", "submit"):
        acknowledged = "--ack-unknown" in args
        args = [a for a in args if a != "--ack-unknown"]
        if name == "submit":
            if len(args) != 1:
                raise BrokerError("INVALID_INPUT", "Use submit LOCAL_ID [--ack-unknown].")
            await confirm_submit(client, args[0], acknowledged)
        else:
            order = await client.prepare_order(parse_order(args), acknowledge_unknown=acknowledged)
            if name == "trade":
                await confirm_submit(client, order.local_id, acknowledged)
            else:
                show_order(order)
    elif name == "orders":
        orders = client.list_orders()
        if not orders:
            print("No orders for the current account. Log in to view its journal.")
        for order in orders:
            show_order(order)
    elif name == "order":
        if len(args) not in (1, 2) or (len(args) == 2 and args[1] != "--local"):
            raise BrokerError("INVALID_INPUT", "Use order LOCAL_ID [--local].")
        show_order(await client.get_order(args[0], reconcile="--local" not in args))
    elif name == "logout":
        invalidated = await client.logout()
        print(
            "Session closed; remote logout confirmed."
            if invalidated
            else "Session closed locally; remote logout was not confirmed."
        )
    else:
        raise BrokerError("INVALID_INPUT", "Unknown command. Use help to see available commands.")
    return True


async def run(args: argparse.Namespace) -> None:
    if not sys.stdin.isatty():
        raise BrokerError(
            "INTERACTIVE_REQUIRED",
            "Run in a terminal. Password entry and order review require an interactive session.",
        )
    client = IQOptionClient(
        args.journal,
        transport=WebSocketTransport(auth_style=args.auth_style, request_timeout=args.timeout),
    )
    print("NSL IQ Option terminal. Type help for commands; login to authenticate.")
    try:
        while True:
            try:
                line = await prompt("iq> ")
                words = shlex.split(line)
                if words and not await command(client, words):
                    break
            except EOFError:
                break
            except BrokerError as error:
                print(str(error))
            except ValueError:
                print("INVALID_INPUT: Could not parse the command.")
    finally:
        await client.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Interactive IQ Option binary/turbo trading terminal"
    )
    parser.add_argument("--journal", type=Path, default=Path(".runtime/orders.sqlite3"))
    parser.add_argument("--auth-style", choices=("modern", "legacy"), default="modern")
    parser.add_argument(
        "--timeout", type=float, default=10.0, help="Broker response timeout in seconds"
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Log operations and error codes, without message bodies",
    )
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error("timeout must be positive")
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        print("\nSession closed.")
    except BrokerError as error:
        parser.exit(1, f"{error}\n")
