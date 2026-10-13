# NSL IQ Option

Python broker adapter with a native desktop GUI and interactive terminal implementing [spec.md](spec.md). It supports credential login, practice/real balance selection, dynamic binary/turbo symbol discovery, manual CALL/PUT orders, a durable SQLite order journal, and settlement/history recovery.

The protocol is based on public client source and live read-only checks. **Login, profile/balance loading, and binary/turbo symbol discovery were verified against IQ Option on 2026-10-13. Order placement and settlement remain unverified.** Account verification challenges are detected and reported; OTP completion is not implemented until its actual flow is verified. See [protocol evidence and limitations](docs/protocol.md).

## Install and run

Requires Python 3.11 or later. Development tests were run on Python 3.14.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[gui]'
.venv/bin/nsl-iqoption-gui
```

The GUI starts disconnected. Open **Account**, enter your identifier and password, and select **Connect account**. The password field is masked and cleared when sign-in begins; credentials are not saved to disk.

The **Workspace** screen shows balances, searchable markets, binary/turbo and OTC filters, and an order ticket. Select a product row, select CALL or PUT, enter a stake, select a supported duration, and choose **Review order**. The review displays the frozen account, amount, direction, and exact expiration. Canceling sends nothing; **Place practice order** or **Place real order** submits that intent once. An expired purchase window disables submission.

The **Orders** screen shows recorded status, broker ID, and results. Select an accepted or unknown order and choose **Refresh selected status** to reconcile it against broker history. Prepared intents can be reviewed from this screen. If unresolved orders exist on the selected balance, review them before checking **Reviewed unresolved orders** on the ticket to allow another intent.

Broker requests and the SQLite journal run in a background worker. The UI stays responsive during login, refresh, and submission. Closing the window cancels the current operation, closes the broker session, and preserves any uncertain order state. Run one interface per journal at a time.

You can also launch the desktop with `.venv/bin/python -m nsl_iqoption.gui`. Qt is an optional dependency; a terminal-only installation can use `pip install -e .` without the `gui` extra.

## Terminal interface

```sh
.venv/bin/nsl-iqoption
```

Credentials are entered at a masked local prompt.

```text
iq> login
IQ Option identifier: your-account@example.com
Password:
iq> balances
iq> symbols --refresh
iq> symbols EURUSD --open
iq> prepare EURUSD-OTC turbo call 1.00 1
```

Use a symbol/product and duration actually returned for your account. `prepare` displays the exact expiration and saves an intent without sending an order. Submit the displayed local ID:

```text
iq> submit LOCAL_ID_FROM_PREPARE
Type submit to place this order, or press Enter to cancel: submit
iq> order LOCAL_ID_FROM_PREPARE
iq> orders
iq> logout
iq> quit
```

`trade SYMBOL PRODUCT DIRECTION AMOUNT MINUTES` combines preparation and review. It still requires typing `submit` before sending. Default balance selection is practice. Real balances require explicit `balance ID --real` in the current session; every order review displays the mode, currency, and frozen balance ID.

Orders interrupted after transmission may have started become `UNKNOWN` and are never resent automatically. Reopening the journal preserves that state. Inspect `orders` and `order ID`; an intentional new order on the affected balance requires `--ack-unknown`. History reconciliation requires a known broker ID or an echoed request ID, so similar-looking trades do not turn uncertain submissions into a claimed success.

## Configuration

```sh
.venv/bin/nsl-iqoption --help
.venv/bin/nsl-iqoption --auth-style legacy --timeout 15
.venv/bin/nsl-iqoption-gui --auth-style legacy --timeout 15
```

`modern` uses the candidate `authenticate` WebSocket envelope; `legacy` selects the older `ssid` envelope explicitly. Read-only initialization tries version 4.0 then 3.0 on an incompatible schema/request. Orders are sent once with version 1.0; there is no order retry or protocol fallback after sending.

The default journal is `.runtime/orders.sqlite3`. Preserve it to retain recovery information. Runtime data, logs, private captures, and virtual environments are ignored by Git. Use one running terminal per journal.

## Importable API

```python
from decimal import Decimal
from nsl_iqoption import IQOptionClient, OrderInput, Product

async def inspect(identifier, password):
    client = IQOptionClient()
    try:
        session = await client.login(identifier, password)
        catalog = await client.list_symbols(refresh=True)
        return session, catalog
    finally:
        await client.close()
```

`prepare_order(OrderInput(...))` returns a journaled intent. `submit_order(local_id)` is the explicit trading action for application callers. The terminal handles the review prompt; API callers must obtain the user's trade instruction before invoking it.

## Tests

```sh
.venv/bin/python -m unittest discover -s tests -v
```

Tests use synthetic fixtures, an offscreen Qt GUI, and a local HTTP/WebSocket server. They never call IQ Option or place broker orders. Desktop tests are skipped if the optional Qt dependency is absent. They cover password masking/clearing, per-product metadata, real-balance selection, canceled/confirmed reviews, expired windows, unknown-order handling, and access to the broker on its worker thread. Live acceptance requires checking authenticated balances and symbols against the normal client, then submitting one user-selected practice order and comparing its ID, fields, and settlement. No live order test runs implicitly.

Initial expiration support is limited to metadata-listed 1–5 minute turbo durations and binary durations in 15-minute steps. Missing eligibility, currency precision, or expiration information blocks order preparation. Payouts are shown as unknown when their candidate endpoint is unavailable. Current implementation uses the minimum candidate order body; accounts requiring extra quote/payout fields need live protocol validation before those fields can be added.
