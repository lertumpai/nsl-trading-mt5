# Protocol evidence and verification status

Implementation date: 2026-10-13. The adapter is an independently implemented client using public source and live read-only protocol checks. No proprietary client code was copied. Login, catalog, chart, and one approved EURUSD practice Turbo order have passed authenticated checks, including acceptance, settlement/history, and journal reopening. Fixtures in `tests` are synthetic and contain no account credentials or personal broker data.

Live read-only verification on 2026-10-13:

- HTTP and WebSocket authentication succeeded with a user-authorized account.
- Profile and balances loaded successfully, including one practice balance.
- Initialization version 4.0 returned 270 binary and 247 turbo product entries. These counts include closed/inactive products and separate entries for products sharing an underlying.
- The broker sends a generic `result` acknowledgement with `msg: {"success": true}` before data responses. This must not resolve profile, balance, initialization, or order-data requests; subscription acknowledgements may complete on it.
- Some inactive binary products contain schedule intervals with the end before the start. These intervals are discarded as having no open instant; their endpoints are never swapped.
- Verification sessions were logged out and closed. No orders were prepared or submitted, and credentials were not saved.

| Operation | Implemented candidate | Evidence |
| --- | --- | --- |
| HTTP login | Auth host `/api/v2/login`, JSON identifier/password; inspect `ssid` response/cookies | [Python login source](https://github.com/iqoptionapi/iqoptionapi/blob/master/iqoptionapi/http/login.py), [JavaScript client](https://github.com/LuKks/iqoption/blob/master/index.js) |
| WebSocket | `ws.iqoption.com/echo/websocket`; explicit modern/legacy authentication selection | [JavaScript client](https://github.com/LuKks/iqoption/blob/master/index.js), [Older SSID channel](https://github.com/iqoptionapi/iqoptionapi/blob/master/iqoptionapi/ws/chanels/ssid.py) |
| Profile/balances | `core.get-profile` and `get-balances` 1.0; account types 1 real, 4 practice | [Profile/transport client](https://github.com/celiovmjr/iq-core/blob/main/iq_core/websocket/client.py), [Account types](https://github.com/celiovmjr/iq-core/blob/main/iq_core/entities/account.py) |
| Initialization/payout | `get-initialization-data` 4.0 with read-only 3.0 fallback; trading group parameters 2.0 | [Instrument service](https://github.com/celiovmjr/iq-core/blob/main/iq_core/services/instrument_service.py) |
| Historical candles | Read-only `get-candles` 2.0 with active ID, size in seconds, broker time, count, and split normalization; OHLC in `candles` response | [Public request channel](https://github.com/iqoptionapi/iqoptionapi/blob/master/iqoptionapi/ws/chanels/candles.py), [Response handler](https://github.com/iqoptionapi/iqoptionapi/blob/master/iqoptionapi/ws/received/candles.py) |
| Order | `binary-options.open-option` 1.0 with balance, active, type, direction, expiration, price | [Minimum buy channel](https://github.com/iqoptionapi/iqoptionapi/blob/master/iqoptionapi/ws/chanels/buyv3.py) |
| Expiration | Broker time, aligned minute/quarter-hour candidate slots and purchase cutoff | [Expiration source](https://github.com/iqoptionapi/iqoptionapi/blob/master/iqoptionapi/expiration.py) |
| Position events/history | Position-change subscription 3.0; history 2.0 with balance and product filtering | [Event client](https://github.com/LuKks/iqoption/blob/master/index.js), [History query](https://github.com/celiovmjr/iq-core/blob/main/iq_core/services/binary_trade_service.py) |
| Logout | Auth host `/api/v1.0/logout` | [Logout source](https://github.com/iqoptionapi/iqoptionapi/blob/master/iqoptionapi/http/logout.py) |

Interpretations still needing further live confirmation:

- Initialization `option.expiration_times` keys are interpreted as seconds. Ambiguous values below 60 are not promoted to guessed minute durations.
- `option.deadtime` is interpreted as seconds. When absent, candidate cutoffs are 30 seconds for turbo and 300 seconds for binary. Unsupported products/durations are rejected.
- A closed history row's `close_profit` is provisionally displayed as gross return and `pnl` as net profit. These mappings follow the [public result model](https://github.com/celiovmjr/iq-core/blob/main/iq_core/entities/trade_result.py); confirm them against the broker UI before relying on exported results. Missing fields remain unknown.
- Some clients send extra quote/refund/profit fields. The minimum payload was sufficient for the approved EURUSD practice Turbo order on 2026-10-13. Other products/accounts remain unverified. No automatic resubmission with another payload is permitted.
- The HTTP/OTP protocol has account-specific challenges. Detection is implemented, but challenge completion is intentionally unavailable until its endpoint, token, and code flow are observed and verified.
- Unknown orders without a broker ID or echoed request ID cannot be correlated conclusively by matching symbol, amount, and time. They remain unknown for manual inspection.

Live validation checklist:

1. Authenticate via the terminal and compare profile/balances to the account UI.
2. Compare account-specific binary/turbo catalog, OTC availability, schedules, and expiration choices.
3. Prepare an agreed practice intent; confirm the displayed expiration and mode.
4. Submit once and compare broker ID and all trade fields to the UI.
5. Compare settlement, gross return, and net profit; test reconnect and journal recovery.
6. Add sanitized captures to fixtures, record actual working versions/account mode/date, and update this document's live status. Exclude credentials, tokens, cookies, and personal identifiers.

## Chart and binary ticket update (2026-10-13)

The desktop now requests 120 historical candles for the selected active at 60, 300, 900, 3600, or 86400 seconds. It polls every three seconds on a separate, cancelable worker task. Chart changes cannot submit an order or change its expiry. Responses from a previous symbol/timeframe are ignored; unavailable or malformed data is reported rather than fabricated. Candle timestamps, interval lengths, finite OHLC, low/high containment, uniqueness, and absence of future starts are validated. The current unfinished candle is permitted.

The request/response schema follows public client source, not an official supported broker API. The first chart implementation used synthetic tests. A subsequent authenticated check on 2026-10-13 returned valid EURUSD candles at all five timeframes; OTC and closed-market history still need separate validation.

The ticket merges expiration choices by active ID and symbol name and maps each choice back to its original Binary or Turbo metadata. One-minute expiry retains the existing Turbo path and does not relabel or alter the underlying order body. Payout, schedule, stake limits, and cutoff are read from the routed product. The UI presents Higher/Lower, investment amount, expiry, payout/profit estimate, purchase-window countdown, and positions as described in [IQ Option's official binary options guide](https://blog.iqoption.com/en/how-to-trade-binary-options-an-a-z-guide-from-iq-option/). Digital strike selection, Blitz, early sell/refund quotes, and other product APIs remain unsupported; no unverified trading payloads were added.

## Authenticated chart and preparation validation (2026-10-13)

Using an explicitly supplied account, the adapter authenticated and loaded a USD practice balance. EURUSD candle requests returned validated broker data for 60, 300, 900, 3600, and 86400 second intervals. These checks were read-only.

Order preparation originally failed with METADATA_STALE. The two optional trading-group payout requests took about 20 seconds after initialization had arrived, making the instrument eligibility older than its five-second limit. The broker's initialization response already contains `option.profit.commission` (12 for the inspected EURUSD Turbo product, representing an 88 percent profit offer). Parsing that optional field directly and publishing eligibility immediately reduced catalog loading from about 22 seconds to 2 seconds. Missing commissions can use an optional background lookup; it cannot postpone eligibility or rewrite its refresh timestamp. Late payout results are discarded when the catalog generation changes.

The fixed adapter prepared a EURUSD Turbo CALL intent with a one-minute expiry on the practice balance in about 0.37 seconds. That probe did not submit the intent. The subsequent user-approved practice verification completed submission and settlement; see below. No passwords, authentication tokens, or profile details are included in these notes or fixtures.

## Approved practice order verification (2026-10-13)

The user approved one EURUSD Higher/CALL order with a one-minute expiry and a 30 USD practice stake. The adapter selected the USD practice balance, prepared a fresh aligned expiration, and submitted the minimum `binary-options.open-option` 1.0 body once with option type 3. The correlated `option` response contained a broker ID, followed by `position-changed` events identifying the same external ID and `turbo-option` product.

The position closed with `close_reason` and raw result `equal`; open and close quotes were identical. The broker reported `invest=30`, `close_profit=30`, and `pnl=0`. The adapter recorded SETTLED, gross return 30 USD, and net profit 0 USD. A later authenticated history read matched the exact broker ID, practice balance, product, stake, direction, expiration, quotes, and result. The practice balance was 10000 USD afterward. Reopening the journal preserved the same settled order; no second order was submitted. Live win/loss results, real-money execution, other order products, and disconnect-during-submission behavior were not tested by this verification.

All five EURUSD candle requests also returned 120 validated candles, matching the desktop request size. The exact broker reference and sanitized history evidence are stored only in ignored `.runtime/practice-verification-result.json`; the account-scoped journal is `.runtime/practice-verification.sqlite3`. Credentials and session tokens are absent. Synthetic regressions cover returned-stake settlement, ignoring interim open-position P/L, persisted settled state, and refusal to claim a settled intent again.
