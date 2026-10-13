# Protocol evidence and verification status

Implementation date: 2026-10-13. The adapter is an independently implemented client using public source and live read-only protocol checks. No proprietary client code was copied. Login and catalog checks have passed; order acceptance is pending. Fixtures in `tests` are synthetic and contain no account credentials or personal broker data.

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
| Order | `binary-options.open-option` 1.0 with balance, active, type, direction, expiration, price | [Minimum buy channel](https://github.com/iqoptionapi/iqoptionapi/blob/master/iqoptionapi/ws/chanels/buyv3.py) |
| Expiration | Broker time, aligned minute/quarter-hour candidate slots and purchase cutoff | [Expiration source](https://github.com/iqoptionapi/iqoptionapi/blob/master/iqoptionapi/expiration.py) |
| Position events/history | Position-change subscription 3.0; history 2.0 with balance and product filtering | [Event client](https://github.com/LuKks/iqoption/blob/master/index.js), [History query](https://github.com/celiovmjr/iq-core/blob/main/iq_core/services/binary_trade_service.py) |
| Logout | Auth host `/api/v1.0/logout` | [Logout source](https://github.com/iqoptionapi/iqoptionapi/blob/master/iqoptionapi/http/logout.py) |

Interpretations still needing further live confirmation:

- Initialization `option.expiration_times` keys are interpreted as seconds. Ambiguous values below 60 are not promoted to guessed minute durations.
- `option.deadtime` is interpreted as seconds. When absent, candidate cutoffs are 30 seconds for turbo and 300 seconds for binary. Unsupported products/durations are rejected.
- A closed history row's `close_profit` is provisionally displayed as gross return and `pnl` as net profit. These mappings follow the [public result model](https://github.com/celiovmjr/iq-core/blob/main/iq_core/entities/trade_result.py); confirm them against the broker UI before relying on exported results. Missing fields remain unknown.
- Some clients send extra quote/refund/profit fields. The minimum payload's sufficiency is unverified. No automatic resubmission with another payload is permitted.
- The HTTP/OTP protocol has account-specific challenges. Detection is implemented, but challenge completion is intentionally unavailable until its endpoint, token, and code flow are observed and verified.
- Unknown orders without a broker ID or echoed request ID cannot be correlated conclusively by matching symbol, amount, and time. They remain unknown for manual inspection.

Live validation checklist:

1. Authenticate via the terminal and compare profile/balances to the account UI.
2. Compare account-specific binary/turbo catalog, OTC availability, schedules, and expiration choices.
3. Prepare an agreed practice intent; confirm the displayed expiration and mode.
4. Submit once and compare broker ID and all trade fields to the UI.
5. Compare settlement, gross return, and net profit; test reconnect and journal recovery.
6. Add sanitized captures to fixtures, record actual working versions/account mode/date, and update this document's live status. Exclude credentials, tokens, cookies, and personal identifiers.
