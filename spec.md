# IQ Option integration specification

Date: 2026-10-13

Status: adapter, terminal, and GUI implemented. Live login, balances, and binary/turbo symbol discovery verified on 2026-10-13; live trade verification remains pending.

## 1. Objective

Build a program that can:

1. Successfully authenticate an IQ Option account.
2. Show the account's available binary option symbols, including OTC instruments where available.
3. Submit a user-directed binary option trade and show its broker-confirmed status and result.

Success means working against IQ Option's live service. Mock responses alone do not satisfy these requirements. A trade on an actual IQ Option practice balance counts as live protocol verification; an offline simulated trade does not.

## 2. Repository context and implementation baseline

The current checkout contains no application source files. Git history contains an MQL5 grid EA, but the latest commit removed it. This specification does not assume that EA is still the application or that its trading strategy should be restored.

Proposed first implementation:

- A Python asynchronous broker adapter with typed application models.
- A native desktop GUI for login, balance selection, symbol listing, manual order review/submission, and order status; retain the terminal interface for the same workflow.
- An importable service API so a future GUI or MT5 bridge can use the same integration.
- A local SQLite order journal for recovery and duplicate prevention.

The initial interface is a single running session so credentials and session tokens need not be persisted between commands. Transport library and Python version selection occur during implementation and must be documented in the project setup instructions.

MT5 terminal integration is a separate interface decision. If required, an MQL5 caller should communicate with a local broker service; the broker adapter remains responsible for IQ Option authentication and WebSocket messages. MT5 `OrderSend` must not be treated as an IQ Option order submission mechanism.

## 3. Scope

Required: login and logout, session health, profile and balances, dynamic binary/turbo instrument discovery, supported expiration selection, manual CALL/PUT orders, broker rejection handling, settlement tracking, and recovery after connection loss.

Binary and turbo products must retain their separate availability, payout, and expiration rules even if presented together as binary options. The same underlying may support one product while the other is unavailable.

Deferred: strategies, martingale/grid behavior, scheduled trading, digital options, blitz options, CFDs, deposits, withdrawals, social login, and charts.

## 4. Protocol evidence and research plan

The following are candidates found in public client source, not a verified contract for the current service or every account. Implementation must keep endpoint and message-version choices in one protocol module and record which variant was verified.

| Operation | Candidate observed in source | Evidence |
| --- | --- | --- |
| HTTP login | `POST https://auth.iqoption.com/api/v2/login`, fields `identifier` and `password` | [Python login implementation](https://github.com/iqoptionapi/iqoptionapi/blob/master/iqoptionapi/http/login.py) |
| WebSocket endpoint and authentication | `wss://ws.iqoption.com/echo/websocket`; an `authenticate` envelope carrying an `ssid` | [JavaScript client](https://github.com/LuKks/iqoption/blob/master/index.js) |
| Older authentication variant | A top-level `ssid` message | [SSID channel](https://github.com/iqoptionapi/iqoptionapi/blob/master/iqoptionapi/ws/chanels/ssid.py) |
| Balance query | `sendMessage` containing `get-balances`, version `1.0` | [Balance channel](https://github.com/iqoptionapi/iqoptionapi/blob/master/iqoptionapi/ws/chanels/get_balances.py) |
| Instrument initialization | `get-initialization-data`; examples use `3.0` or `4.0` | [JavaScript client](https://github.com/LuKks/iqoption/blob/master/index.js), [Instrument service](https://github.com/celiovmjr/iq-core/blob/main/iq_core/services/instrument_service.py) |
| Binary order | `binary-options.open-option`, version `1.0`; observed type IDs are `1` for binary and `3` for turbo | [Buy channel](https://github.com/iqoptionapi/iqoptionapi/blob/master/iqoptionapi/ws/chanels/buyv3.py) |
| Expiration calculation | Minute-aligned short expirations, purchase cutoff, and different longer-expiration slots | [Expiration implementation](https://github.com/iqoptionapi/iqoptionapi/blob/master/iqoptionapi/expiration.py) |
| Settlement/history query | `portfolio.get-history-positions`, version `2.0`, scoped to user and balance | [Binary trade service](https://github.com/celiovmjr/iq-core/blob/main/iq_core/services/binary_trade_service.py) |

Research procedure:

1. Inspect public implementations for candidate messages without copying insecure transport settings or assuming they still work.
2. Observe the normal IQ Option web client's requests and WebSocket traffic in a user-authorized account session when needed to resolve discrepancies.
3. Verify HTTP response handling, cookies/token location, WebSocket authentication, profile, balances, instrument metadata, and server time before attempting an order.
4. Verify one explicitly selected practice order, its acknowledgement, and its settlement.
5. Save sanitized message fixtures and a protocol note identifying the tested versions, date, account mode, and unresolved differences. Remove credentials, session tokens, cookies, and personal identifiers from shared fixtures.

Authentication challenges must follow the normal account flow. An unsupported challenge is a reported limitation requiring implementation, not a successful login.

## 5. Functional requirements

### FR-1: Authentication and session

- Accept an email/identifier and a password through a masked prompt or an equivalent local secret input.
- Validate the HTTP response and extract the session token from the verified response format or cookie jar. A status code of 200 alone is insufficient.
- Authenticate the WebSocket and obtain a valid profile and balance response before reporting login success.
- Distinguish invalid credentials, expired session, additional verification, rate limiting, network timeout, and protocol incompatibility.
- If OTP/2FA is required, expose a challenge state and accept the code through the normal flow once that flow is verified. CAPTCHA or browser-only verification must be surfaced clearly.
- Keep secrets in memory by default. Never print the password, `ssid`, authorization headers, or cookies in logs or error output.
- On logout, stop reconnect attempts, close transports, clear in-memory secrets, and invalidate the remote session where supported.

Session states: `DISCONNECTED`, `AUTHENTICATING`, `CHALLENGE_REQUIRED`, `CONNECTED`, `RECONNECTING`, `SESSION_EXPIRED`, and `ERROR`.

### FR-2: Account and balance selection

- Discover actual balance IDs, modes, currencies, and amounts from the broker.
- Default to a verified practice balance. If none exists, show a selection error; do not silently select a real balance.
- Require explicit real-mode selection before accepting a real order. Show the selected mode and currency next to the order fields.
- Keep the selected balance ID in the validated order intent. Changing the active account must not redirect an already prepared or submitted order.
- Refresh balance data after reconnect and after order/settlement events.

### FR-3: Binary symbol list

- Fetch account-specific instrument metadata dynamically; do not use a hardcoded symbol-to-ID table as the authoritative catalog.
- Preserve separate records per `(active_id, product_type)`.
- Show symbol, display name, active ID, product type, OTC flag, market status, payout when known, and available expirations when known.
- Support search, open-only filtering, and OTC filtering. Default to showing all discovered binary/turbo instruments with their status.
- Determine tradability from account eligibility, enabled/suspended state, current schedule, and purchase windows using synchronized broker time.
- Represent missing payout, schedule, or expiration information as unknown. Unknown availability must not be interpreted as open.
- Refresh the catalog on user request and reconnect; apply verified market/payout change events where supported. Display the last refresh time and stale state.
- Distinguish an empty, valid catalog from a failed or incompatible metadata response.

### FR-4: Order preparation and submission

Input: symbol or active ID, product type, selected balance, CALL/PUT direction, stake in account currency, and a supported expiration choice.

- Validate authentication, balance ownership/mode, symbol/product availability, positive finite stake, currency precision, known broker limits, available funds, and expiration eligibility.
- Use decimal arithmetic for money. Conversion to the broker's wire format must preserve account currency precision.
- Compute an absolute expiration timestamp using fresh broker time and the verified product schedule. A nominal one-minute selection may expire at an aligned clock boundary; show the actual expiration and remaining duration.
- Reject expired purchase windows, stale time, stale eligibility metadata, or unsupported expiration choices. Do not silently substitute another expiration or product.
- Present the complete order intent, then submit only on the user's explicit trade action. Prevent repeated submission while that intent is pending.
- Send one uniquely correlated broker request and retain the local order ID, request ID, balance ID, and broker order ID when received.
- Mark the order accepted only after a broker acknowledgement identifies the actual option/order. A transport send or generic success envelope alone is insufficient.
- Surface broker rejections with a normalized code and a sanitized broker explanation.

Observed minimum candidate order body, subject to live verification:

```json
{
  "name": "sendMessage",
  "request_id": "unique-request-id",
  "msg": {
    "name": "binary-options.open-option",
    "version": "1.0",
    "body": {
      "user_balance_id": 123456,
      "active_id": 76,
      "option_type_id": 3,
      "direction": "call",
      "expired": 2000000040,
      "price": 1.0
    }
  }
}
```

These values are illustrative and must never be submitted as defaults. Some public clients also send quote, refund, or payout fields; their necessity and units must be established from the working client and practice verification. See the [extended order implementation](https://github.com/celiovmjr/iq-core/blob/main/iq_core/services/binary_trade_service.py).

### FR-5: Order status, settlement, and ambiguity

- Persist order intent before sending. Track `PREPARED`, `SUBMITTING`, `ACCEPTED`, `REJECTED`, `UNKNOWN`, and `SETTLED` as distinct states.
- Consume verified order/position events and use balance-scoped history queries for reconciliation.
- Show stake, actual expiration, broker order ID, settlement state, gross return when available, and net profit/loss with currency.
- Distinguish gross return from net profit. Do not infer a win or loss solely from a balance change or missing history row.
- Deduplicate repeated events and handle settlement arriving before an acknowledgement.
- If timeout, disconnect, or process termination occurs after sending may have begun, mark the order `UNKNOWN`. Never automatically resend it.
- Reconcile unknown orders against broker history, including pagination. If matching is ambiguous, retain `UNKNOWN` and show the evidence; do not guess that the order failed.
- Persist unresolved orders across restarts and disclose them before permitting an intentional replacement order.

## 6. Architecture and application contract

| Component | Responsibility |
| --- | --- |
| Authentication client | HTTP login/challenge flow, cookie handling, session lifecycle |
| WebSocket transport | Single reader, message dispatch, request correlation, heartbeats, server-time synchronization |
| Protocol adapter | Message envelopes, versions, parsers, and schema compatibility |
| Account service | Profile, balances, explicit mode selection |
| Instrument service | Catalog normalization, schedules, expirations, metadata freshness |
| Trading service | Validation, order preparation/submission, status reconciliation |
| Order journal | Durable intents, request IDs, broker IDs, state transitions, outcomes |
| Terminal interface | User input, account selection, symbol display, manual trades, status/errors |
| Desktop interface | Native Qt workspace, account connection, market filters, order ticket/review, and order history; communicate with a background broker worker using immutable snapshots |

Service methods:

```text
login(identifier, secret) -> Session | AuthChallenge
complete_challenge(challenge_id, code) -> Session
list_balances() -> list[Balance]
select_balance(balance_id) -> Balance
list_symbols(filters, refresh) -> SymbolCatalog
prepare_order(order_input) -> PreparedOrder
submit_order(local_order_id) -> Order
get_order(local_order_id, reconcile) -> Order
list_orders(filters) -> list[Order]
logout() -> None
```

Stable application models must not expose raw credentials or token fields. IDs must retain their exact value; use strings at external interfaces where numeric precision could be lost. Symbol records must retain product-specific metadata rather than merging it by display name.

Normalized error categories include `INVALID_CREDENTIALS`, `AUTH_CHALLENGE_REQUIRED`, `SESSION_EXPIRED`, `PROTOCOL_UNSUPPORTED`, `NETWORK_TIMEOUT`, `ACCOUNT_UNAVAILABLE`, `SYMBOL_UNAVAILABLE`, `METADATA_STALE`, `INVALID_AMOUNT`, `INSUFFICIENT_BALANCE`, `INVALID_EXPIRATION`, `BROKER_REJECTED`, and `ORDER_STATUS_UNKNOWN`.

## 7. Transport and storage requirements

- Verify TLS certificates for HTTPS and WSS.
- Use finite, configurable timeouts; proposed initial defaults are 15 seconds for HTTP/WebSocket connection operations and 10 seconds for individual broker responses.
- Register the pending response handler before sending a request. Handle correlated responses and unsolicited events separately.
- Support out-of-order responses, malformed messages, late replies, and explicit broker errors without leaking pending requests.
- Maintain server time from verified time-sync events using a monotonic local clock between samples. Proposed stale-time threshold is 10 seconds; tune it against observed event frequency.
- Automatically reconnect read-only subscriptions with bounded exponential backoff and jitter. Stop automatic authentication retries when credentials or account verification require user action.
- Require fresh authentication, balances, instrument eligibility, and server time before allowing new trades after reconnect.
- Proposed instrument refresh interval is 30 seconds, with refresh before submission if eligibility is older than 5 seconds. Account/product rules may require tighter freshness.
- Bind any future local bridge to loopback and authenticate its callers. IQ Option secrets remain in the broker process.
- Keep runtime secrets, logs, journals, and private captures out of Git. Logs should include timestamps, local/request/order IDs, operation names, durations, and sanitized error categories.

## 8. User workflow

1. Start the program and enter credentials locally.
2. Complete any supported verification challenge.
3. See authenticated session status and available balances; select a practice or explicitly selected real balance.
4. List/search binary and turbo symbols and inspect market status and expirations.
5. Choose the instrument/product, CALL or PUT, stake, and expiration.
6. Review the exact balance, stake, direction, and expiration; submit the order once.
7. See acceptance or rejection and follow the order through settlement, including any unresolved status.
8. Log out.

This specification authorizes design of real-account support, but does not specify an actual real-money order to execute during development. Live verification should use a user-selected practice balance and agreed order parameters.

## 9. Acceptance criteria

| Goal | Required evidence |
| --- | --- |
| Successful login | Real HTTP and WebSocket authentication; valid profile and broker balances; logout works; invalid credentials produce a clear failure |
| Binary symbol list | A live account catalog with exact active IDs and separate binary/turbo availability; compare displayed entries and closed status with the broker UI; OTC entries appear when returned |
| Trading from this program | A user-authorized practice order submitted by the program appears in IQ Option with matching balance, symbol, direction, stake, and expiration; broker order ID and settlement are recorded |
| Reliable errors | Closed market, invalid amount, expired session, and unsupported expiration fail clearly; rejected orders are not reported as accepted |
| Duplicate prevention | A disconnect/timeout after transmission produces `UNKNOWN` and reconciliation without automatic resubmission |
| Secret handling | Shared fixtures/logs contain no credentials, tokens, cookies, or personal profile data |

If the test account has no binary entitlement or no currently open instruments, report the observed limitation and leave trading acceptance pending. Never manufacture symbols or call that limitation a passed trade test.

## 10. Validation plan

Automated tests should cover protocol parsing with sanitized fixtures; request correlation and unsolicited events; login failure/challenge/session expiry; product-specific symbol availability; server-time and expiration boundaries; currency/amount validation; broker rejection; and late/duplicate acknowledgements.

Recovery tests must cover disconnection before send, during send, after broker acceptance but before acknowledgement, and before settlement. Verify that restarting preserves unresolved intent and cannot automatically create a duplicate order.

Live checks proceed in order: authentication, balances, symbol comparison, one agreed practice order, settlement/history comparison, and recovery. Live order tests must be opt-in and excluded from routine automated test runs. Authentication, balance loading, and catalog discovery have passed live checks with a user-authorized account. No credentials are stored in the repository; order/settlement acceptance remains unverified.

## 11. Implementation milestones

1. **Protocol validation:** confirm current login/authentication variants and collect sanitized profile, balance, initialization, time, and error fixtures.
2. **Login:** implement the transport, session state machine, terminal login/logout, and balance selection.
3. **Symbols:** implement dynamic catalog parsing, product separation, eligibility, expirations, freshness, search, and OTC filtering.
4. **Orders:** implement prepared intents, durable journal, exact expiration selection, one-shot submission, acknowledgements, and clear rejections.
5. **Settlement and recovery:** implement status subscriptions/history reconciliation, unknown-order handling, and restart recovery.
6. **Acceptance:** run the agreed practice verification and document results and remaining account-specific limitations.

## 12. Decisions to resolve during implementation

- The desktop GUI is the primary interactive interface. The terminal remains available; MT5 EA integration is deferred.
- Which account's authentication challenges and binary/turbo entitlements must be supported.
- Which live message versions, quote/payout fields, expiration slots, and purchase cutoffs the target account requires.
- Exact practice order parameters for opt-in verification; these must come from the user and live broker metadata.

The specification-first step is complete. Adapter, terminal, and desktop GUI implementations are present. Live login and catalog discovery are verified; live order/settlement acceptance remains pending.
