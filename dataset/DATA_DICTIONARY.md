# คำอธิบายคอลัมน์ CSV ที่ส่งออกจริง

Schema version 1.1.0 อ้างอิงไฟล์ `schema.json` ส่วน `schema_v1/` เป็นแบบร่างก่อนดึงข้อมูล

ค่าที่ไม่มีข้อมูลใช้ช่องว่าง ไม่แทนด้วย 0 ตัวเลขทศนิยมใช้จุด Boolean ใช้ 0/1 เวลาเซิร์ฟเวอร์ไม่ใส่ offset ที่ยังไม่ยืนยัน

บทบาท `input` และ `input_optional` เป็นข้อมูลที่ทราบ ณ เวลานั้น `target` เป็นคำตอบเชิงแผน `future_label` เป็นผลภายหลัง ส่วน `audit`, `split_control`, `join_key` ไม่ใช่ feature ตามค่าเริ่มต้น


## events/M1.csv

| Column | Type | Role | Meaning |
| --- | --- | --- | --- |
| event_id | string | join_key | Unique across broker, source symbol, decision time and setup sequence. |
| timeframe | enum:M1/M5/M15 | context | Timeframe of this row. |
| bar_index | int | context | Latest fully closed bar is 0; earlier bars are negative. Rows are oldest to newest. |
| open_time_server | datetime | context | Original broker chart time; ISO 8601 without an invented UTC offset. |
| close_time_server | datetime | context | Exclusive bar boundary: open time plus timeframe duration. Not last tick time. |
| open_time_utc | datetime_utc | context | UTC conversion only when the historical broker offset is verified. |
| open | float | input | Original OHLC open in quote price units. |
| high | float | input | Original OHLC high in quote price units. |
| low | float | input | Original OHLC low in quote price units. |
| close | float | input | Original OHLC close in quote price units. |
| tick_volume | int | input | Broker tick volume; not centralized FX traded volume. |
| real_volume | float | input_optional | Native real_volume when present. Preserve native zero; consult event availability flag. |
| bar_spread_points | int | input_optional | Native MqlRates spread field in points. Not a full intrabar spread path or guaranteed fill spread. |
| source_m1_count | int | audit | For aggregated bars: count of source M1 rows. Missing where native aggregation cannot be independently verified. |
| gap_before_seconds | int | audit | max(0, open_time minus prior open_time minus timeframe seconds). Includes scheduled closures. |
| gap_class | enum:none/scheduled_close/unexplained/unknown | audit | Scheduled classification only with verified trading-session calendar. |
| feature_valid | bool | audit | 1 only when the required feature history and quality conditions are satisfied. |
| body_price | float | input | abs(close-open), in price units. |
| upper_wick_price | float | input | high-max(open,close), in price units. |
| lower_wick_price | float | input | min(open,close)-low, in price units. |
| body_to_range | float | input | abs(close-open)/(high-low); blank for zero-range bars. |
| return_1bar | float | input | close/prior_close-1; blank if unavailable. Gaps are retained and flagged. |
| atr_14_price | float | input_optional | Causal ATR(14) in price units, definition fixed in schema configuration. |
| range_atr_14 | float | input_optional | (high-low)/atr_14_price; blank when ATR missing or zero. |
| ema_9_price | float | input_optional | Causal EMA(9) of close; never initialize afresh at each event window. |
| ema_21_price | float | input_optional | Causal EMA(21) of close. |
| ema_200_price | float | input_optional | Causal EMA(200) of close. |
| rsi_14 | float | input_optional | Causal Wilder RSI(14) of close. |
| tick_volume_ratio_20 | float | input_optional | Current tick_volume / mean of preceding 20 completed bars, excluding current from denominator. |


## events/M5.csv

| Column | Type | Role | Meaning |
| --- | --- | --- | --- |
| event_id | string | join_key | Unique across broker, source symbol, decision time and setup sequence. |
| timeframe | enum:M1/M5/M15 | context | Timeframe of this row. |
| bar_index | int | context | Latest fully closed bar is 0; earlier bars are negative. Rows are oldest to newest. |
| open_time_server | datetime | context | Original broker chart time; ISO 8601 without an invented UTC offset. |
| close_time_server | datetime | context | Exclusive bar boundary: open time plus timeframe duration. Not last tick time. |
| open_time_utc | datetime_utc | context | UTC conversion only when the historical broker offset is verified. |
| open | float | input | Original OHLC open in quote price units. |
| high | float | input | Original OHLC high in quote price units. |
| low | float | input | Original OHLC low in quote price units. |
| close | float | input | Original OHLC close in quote price units. |
| tick_volume | int | input | Broker tick volume; not centralized FX traded volume. |
| real_volume | float | input_optional | Native real_volume when present. Preserve native zero; consult event availability flag. |
| bar_spread_points | int | input_optional | Native MqlRates spread field in points. Not a full intrabar spread path or guaranteed fill spread. |
| source_m1_count | int | audit | For aggregated bars: count of source M1 rows. Missing where native aggregation cannot be independently verified. |
| gap_before_seconds | int | audit | max(0, open_time minus prior open_time minus timeframe seconds). Includes scheduled closures. |
| gap_class | enum:none/scheduled_close/unexplained/unknown | audit | Scheduled classification only with verified trading-session calendar. |
| feature_valid | bool | audit | 1 only when the required feature history and quality conditions are satisfied. |
| body_price | float | input | abs(close-open), in price units. |
| upper_wick_price | float | input | high-max(open,close), in price units. |
| lower_wick_price | float | input | min(open,close)-low, in price units. |
| body_to_range | float | input | abs(close-open)/(high-low); blank for zero-range bars. |
| return_1bar | float | input | close/prior_close-1; blank if unavailable. Gaps are retained and flagged. |
| atr_14_price | float | input_optional | Causal ATR(14) in price units, definition fixed in schema configuration. |
| range_atr_14 | float | input_optional | (high-low)/atr_14_price; blank when ATR missing or zero. |
| ema_9_price | float | input_optional | Causal EMA(9) of close; never initialize afresh at each event window. |
| ema_21_price | float | input_optional | Causal EMA(21) of close. |
| ema_200_price | float | input_optional | Causal EMA(200) of close. |
| rsi_14 | float | input_optional | Causal Wilder RSI(14) of close. |
| tick_volume_ratio_20 | float | input_optional | Current tick_volume / mean of preceding 20 completed bars, excluding current from denominator. |


## events/M15.csv

| Column | Type | Role | Meaning |
| --- | --- | --- | --- |
| event_id | string | join_key | Unique across broker, source symbol, decision time and setup sequence. |
| timeframe | enum:M1/M5/M15 | context | Timeframe of this row. |
| bar_index | int | context | Latest fully closed bar is 0; earlier bars are negative. Rows are oldest to newest. |
| open_time_server | datetime | context | Original broker chart time; ISO 8601 without an invented UTC offset. |
| close_time_server | datetime | context | Exclusive bar boundary: open time plus timeframe duration. Not last tick time. |
| open_time_utc | datetime_utc | context | UTC conversion only when the historical broker offset is verified. |
| open | float | input | Original OHLC open in quote price units. |
| high | float | input | Original OHLC high in quote price units. |
| low | float | input | Original OHLC low in quote price units. |
| close | float | input | Original OHLC close in quote price units. |
| tick_volume | int | input | Broker tick volume; not centralized FX traded volume. |
| real_volume | float | input_optional | Native real_volume when present. Preserve native zero; consult event availability flag. |
| bar_spread_points | int | input_optional | Native MqlRates spread field in points. Not a full intrabar spread path or guaranteed fill spread. |
| source_m1_count | int | audit | For aggregated bars: count of source M1 rows. Missing where native aggregation cannot be independently verified. |
| gap_before_seconds | int | audit | max(0, open_time minus prior open_time minus timeframe seconds). Includes scheduled closures. |
| gap_class | enum:none/scheduled_close/unexplained/unknown | audit | Scheduled classification only with verified trading-session calendar. |
| feature_valid | bool | audit | 1 only when the required feature history and quality conditions are satisfied. |
| body_price | float | input | abs(close-open), in price units. |
| upper_wick_price | float | input | high-max(open,close), in price units. |
| lower_wick_price | float | input | min(open,close)-low, in price units. |
| body_to_range | float | input | abs(close-open)/(high-low); blank for zero-range bars. |
| return_1bar | float | input | close/prior_close-1; blank if unavailable. Gaps are retained and flagged. |
| atr_14_price | float | input_optional | Causal ATR(14) in price units, definition fixed in schema configuration. |
| range_atr_14 | float | input_optional | (high-low)/atr_14_price; blank when ATR missing or zero. |
| ema_9_price | float | input_optional | Causal EMA(9) of close; never initialize afresh at each event window. |
| ema_21_price | float | input_optional | Causal EMA(21) of close. |
| ema_200_price | float | input_optional | Causal EMA(200) of close. |
| rsi_14 | float | input_optional | Causal Wilder RSI(14) of close. |
| tick_volume_ratio_20 | float | input_optional | Current tick_volume / mean of preceding 20 completed bars, excluding current from denominator. |


## events/event.csv

| Column | Type | Role | Meaning |
| --- | --- | --- | --- |
| event_id | string | join_key | Unique across broker, source symbol, decision time and setup sequence. |
| schema_version | string | audit | Dataset contract version. |
| pair | enum:EURUSD/USDJPY/GBPUSD | context | Normalized folder pair. |
| source_symbol | string | context | Exact broker symbol, including suffix, e.g. EURUSD#. |
| broker_server | string | context | Broker server name. No account number or credentials. |
| decision_time_server | datetime | context | Information cutoff shared by every timeframe and all pre-entry fields. |
| decision_time_utc | datetime_utc | context | Verified UTC equivalent, otherwise blank. |
| server_utc_offset_minutes | int | context | Historical offset at this event, not the current computer offset. |
| time_mapping_source | string | audit | Evidence/version for historical time conversion or unverified. |
| extracted_at_utc | datetime_utc | audit | Actual export timestamp in UTC. |
| price_basis | enum:bid/ask/last/unknown | context | Verified source basis of OHLC. |
| digits | int | context | Broker display precision at the event if verified. |
| point_size | float | context | Price units per broker point, from verified symbol metadata. |
| pip_size | float | context | Price units per pip. Default convention 0.0001 for EURUSD/GBPUSD, 0.01 for USDJPY; verify. |
| real_volume_status | enum:available/not_provided/unverified | audit | Interpret native real_volume zero using broker availability. |
| quote_bid_at_decision | float | input_optional | Last observed Bid tick at or before the decision, never a future quote. |
| quote_ask_at_decision | float | input_optional | Last observed Ask tick at or before the decision. |
| quote_time_server | datetime | audit | Time of the preceding quote, for quote staleness checks. |
| selection_rule_version | string | audit | Immutable setup/selection configuration version. |
| feature_rule_version | string | audit | Immutable feature calculation configuration version. |
| selection_mode | enum:causal_rule/asof_blind_manual/retrospective_manual | audit | Retrospective manual examples are separated from causal training/evaluation. |
| selection_population_id | string | audit | Scan/control sampling population identifier, linked to a stored extraction configuration. |
| sampling_probability | float | audit | Inclusion probability in (0,1] for sampled controls; 1 for an exhaustive deterministic rule scan. Unknown stays blank. |
| pattern_label | enum:trend_pullback/breakout_retest/sweep_reclaim/range_rejection/no_setup/unclear | target | Price-action class assigned without seeing future performance. |
| side_label | enum:buy/sell/no_trade/unlabeled | target | Intended decision label, not a default model input. |
| review_status | enum:unreviewed/accepted/rejected/unclear | audit | Review quality, independent of win or loss. |
| selection_reason | string | audit | Concrete visible structure/trigger, not future profitability. |
| reviewer_id | string | audit | Reviewer identifier or rule engine version. |
| overlap_group_id | string | split_control | Group nearby/overlapping events. Not sufficient by itself to ensure time-purged splits. |
| split | enum:unassigned/train/validation/test/excluded | split_control | Assigned globally by time across pairs after collecting windows and label horizons. |
| quality_status | enum:valid/insufficient_history/missing_data/time_unverified/needs_review | audit | Data eligibility for the intended training task. |
| quality_notes | string | audit | Specific missing data, ambiguity or exclusions. |
| news_context_status | enum:verified/unavailable/not_collected | audit | Do not infer absence of news from missing historical calendar data. |
| minutes_to_next_scheduled_high_impact_event | float | input_optional | Only historical scheduled information known at decision time; never released actual/surprise data. |


## events/plan.csv

| Column | Type | Role | Meaning |
| --- | --- | --- | --- |
| event_id | string | join_key | Unique across broker, source symbol, decision time and setup sequence. |
| plan_id | string | join_key | One primary entry/exit policy per event in v1. |
| entry_rule_version | string | audit | Versioned executable entry rule or draft_manual. |
| exit_rule_version | string | audit | Versioned exit rule including priority and time stop. |
| plan_status | enum:complete/incomplete/no_trade | audit | A complete trade has explicit executable entry, SL, TP and time/cancel rules. |
| side | enum:buy/sell/no_trade | target | Planned side for supervised training. |
| entry_order_type | enum:market_next_tick/stop/limit/none | target | Market orders use first eligible executable quote after decision; never guaranteed signal close fills. |
| trigger_time_server | datetime | target | Time at which entry trigger became observable, no later than decision. |
| entry_reference_price | float | target | Known decision-time order level/reference; next market fill remains unknown. |
| entry_price_basis | enum:ask/bid/none | audit | Buy opens at Ask; sell opens at Bid in quote-based simulation. |
| entry_condition | string | target | Precise observable trigger, linked to known level IDs. |
| entry_expiry_server | datetime | target | Unfilled entry becomes canceled after this time. |
| cancel_condition | string | target | Pre-entry invalidation rule, including structure failure and data/quote constraints. |
| max_spread_points | float | target | Decision/order-time spread constraint, versioned before evaluation. |
| stop_loss_price | float | target | Initial executable stop level; must be valid for planned side. |
| stop_basis_level_id | string | target | Confirmed structure level that fixes invalidation, if used. |
| stop_buffer_points | float | target | Explicit buffer in broker points, fixed from pre-entry policy. |
| take_profit_price | float | target | Primary pre-entry target; no future swing-extreme chosen after the fact. |
| target_basis_level_id | string | target | Previously known opposing structure level, if used. |
| target_policy | string | target | Exact target method. Any fixed distance/ATR multiplier is specified here. |
| max_hold_seconds | int | target | Time stop measured from actual fill. |
| early_exit_condition | string | target | Observable early invalidation or none. |
| trailing_policy | string | target | none in v1 unless a fully specified rule is registered. |
| breakeven_policy | string | target | none in v1 unless a fully specified rule is registered. |
| estimated_commission_pips_roundtrip | float | audit | Known broker commission estimate converted to pips, not assumed zero. |
| slippage_assumption_points | float | audit | Scenario assumption, not historical observed slippage. |
| execution_delay_ms | int | audit | Explicit backtest delay assumption. |


## events/levels.csv

| Column | Type | Role | Meaning |
| --- | --- | --- | --- |
| event_id | string | join_key | Unique across broker, source symbol, decision time and setup sequence. |
| level_id | string | join_key | Unique within event; includes latest/previous confirmed swings, signal extremes, observed pullback extremes and derived targets. Referenced by plan.csv. |
| timeframe | enum:M1/M5/M15/D1 | context | Origin timeframe. D1 is allowed only for known previous-day levels. |
| level_type | enum:swing_high/swing_low/signal_high/signal_low/pullback_high/pullback_low/breakout_zone/range_midpoint | input_optional | Price-action structure known by cutoff. |
| price_low | float | input_optional | Lower edge of price level/zone. |
| price_high | float | input_optional | Upper edge; equals price_low for a point level. |
| origin_time_server | datetime | audit | Origin bar or start of underlying structure. |
| known_at_server | datetime | context | Earliest time at which confirmation was possible. Must be <= decision. |
| detection_rule_version | string | audit | Exact swing/zone algorithm version. |
| left_bars | int | audit | Swing parameter, if applicable. |
| right_confirmation_bars | int | audit | Right-hand confirming bars already closed by known_at; never use them earlier. |
| touch_count_asof | int | input_optional | Number of contacts known by cutoff. |
| status_asof | enum:active/broken/reclaimed | input_optional | Level state using only observations by cutoff. |


## events/outcome.csv

| Column | Type | Role | Meaning |
| --- | --- | --- | --- |
| event_id | string | join_key | Unique across broker, source symbol, decision time and setup sequence. |
| plan_id | string | join_key | Matches the versioned primary plan. |
| outcome_source | enum:simulated_real_ticks/simulated_bars/actual_deals/not_evaluated | audit | Do not describe simulated fills as real trades. |
| label_status | enum:complete/unfilled/canceled/ambiguous/censored/missing_ticks/not_evaluated | audit | No forced win/loss when path or horizon is unresolved. |
| entry_fill_time_server | datetime | future_label | Minute-bar boundary proxy for the simulated execution interval; not an observed exact tick timestamp. See execution_time_resolution and interval_end fields. |
| entry_fill_price | float | future_label | Executable fill including modeled slippage when applicable. |
| exit_time_server | datetime | future_label | Minute-bar boundary proxy for the simulated execution interval; not an observed exact tick timestamp. See execution_time_resolution and interval_end fields. |
| exit_fill_price | float | future_label | Buy liquidates on Bid; sell on Ask, subject to execution model. |
| exit_reason | enum:sl/tp/time_stop/invalidation/unfilled/canceled/unknown | future_label | Terminal event under the registered exit policy. |
| holding_seconds | int | future_label | Elapsed seconds between simulated minute-bar boundary proxies; approximate, not an observed tick holding duration. |
| initial_risk_pips | float | future_label | abs(entry_fill_price-initial_stop_loss_price)/pip_size, fixed at fill. |
| gross_pnl_pips | float | future_label | side_sign*(exit_fill_price-entry_fill_price)/pip_size. Bid/Ask spread and fill slippage already embedded. |
| commission_pips_roundtrip | float | future_label | Additional round-trip commission in pips; blank if not established. |
| financing_cost_pips | float | future_label | Additional financing cost in pips; positive costs reduce net, negative credits increase net. |
| net_pnl_pips | float | future_label | gross_pnl_pips-commission_pips_roundtrip-financing_cost_pips. Do not subtract spread/slippage a second time. |
| net_r | float | future_label | net_pnl_pips/initial_risk_pips; blank if costs or risk missing. |
| mfe_pips | float | future_label | Maximum nonnegative favorable executable liquidation excursion from fill until exit. |
| mae_pips | float | future_label | Maximum nonnegative adverse executable liquidation excursion from fill until exit. |
| mfe_r | float | future_label | mfe_pips/initial_risk_pips. |
| mae_r | float | future_label | mae_pips/initial_risk_pips. |
| same_bar_sl_tp | bool | audit | 1 if M1 bar data alone permits both SL and TP; tick order required to resolve. |
| real_tick_coverage_ratio | float | audit | Measured coverage of required tradable minutes by real ticks, with denominator defined in config. |
| label_end_time_server | datetime | split_control | End of observed outcome or censoring horizon, used for purging. |
| evaluation_rule_version | string | audit | Exact simulator/execution and label implementation version. |
| gross_r | float | future_label | Gross executable-price P&L / initial risk. No commission or measured slippage deduction. |
| execution_model | string | audit | Explicit bar simulation assumptions; not actual trades. |
| commission_status | enum:unknown | audit | Historical round-trip commission has not been established. |
| slippage_status | enum:not_measured | audit | Actual slippage was not collected. |
| ticks_status | enum:not_requested | audit | Real tick history was not requested for this collection. |
| execution_time_resolution | enum:minute_bar_interval_proxy | audit | Fill/exit time is a minute interval boundary proxy, not an observed exact tick. |
| entry_interval_end_server | datetime | future_label | Exclusive end of the minute within which the simulated entry happened. |
| exit_interval_end_server | datetime | future_label | Exclusive end of exit interval, or the boundary used for a time stop. |
| entry_exit_same_bar_ambiguous | bool | audit | Entry and possible exit share a candle and order cannot be resolved. |


## events/ticks.csv

| Column | Type | Role | Meaning |
| --- | --- | --- | --- |
| event_id | string | join_key | Unique across broker, source symbol, decision time and setup sequence. |
| time_msc_raw | int64 | audit | Native MT5 tick time_msc preserved without reinterpretation. |
| time_server | datetime | audit | Tick chart-server time after verified source-time mapping; blank while mapping unresolved. |
| time_utc_msc | int64 | audit | Verified UTC timestamp only if mapping established. |
| sequence | int | audit | Original order within an equal-millisecond timestamp. |
| bid | float | audit | Broker observed Bid. |
| ask | float | audit | Broker observed Ask. |
| last | float | audit | Native Last, if meaningful for source. |
| flags | int | audit | Native MT5 tick flags. |
| spread_points | float | audit | (ask-bid)/point_size when both quotes valid. |
| phase | enum:pre_decision/post_decision | split_control | Post-decision ticks never enter decision-time features. |
| source | enum:broker_real/synthetic | audit | Synthetic observations never presented as broker real ticks. |


## history/M1.csv

| Column | Type | Role | Meaning |
| --- | --- | --- | --- |
| source_symbol | string | context | source_symbol |
| pair | string | context | pair |
| timeframe | enum:M1/M5/M15 | context | Timeframe of this row. |
| open_time_server | datetime | context | Original broker chart time; ISO 8601 without an invented UTC offset. |
| close_time_server | datetime | context | Exclusive bar boundary: open time plus timeframe duration. Not last tick time. |
| open | float | input | Original OHLC open in quote price units. |
| high | float | input | Original OHLC high in quote price units. |
| low | float | input | Original OHLC low in quote price units. |
| close | float | input | Original OHLC close in quote price units. |
| tick_volume | int | input | Broker tick volume; not centralized FX traded volume. |
| real_volume | float | input_optional | Native real_volume when present. Preserve native zero; consult event availability flag. |
| bar_spread_points | int | input_optional | Native MqlRates spread field in points. Not a full intrabar spread path or guaranteed fill spread. |
| gap_before_seconds | int | audit | max(0, open_time minus prior open_time minus timeframe seconds). Includes scheduled closures. |
| gap_class | enum:none/scheduled_close/unexplained/unknown | audit | Scheduled classification only with verified trading-session calendar. |


## history/M5.csv

| Column | Type | Role | Meaning |
| --- | --- | --- | --- |
| source_symbol | string | context | source_symbol |
| pair | string | context | pair |
| timeframe | enum:M1/M5/M15 | context | Timeframe of this row. |
| open_time_server | datetime | context | Original broker chart time; ISO 8601 without an invented UTC offset. |
| close_time_server | datetime | context | Exclusive bar boundary: open time plus timeframe duration. Not last tick time. |
| open | float | input | Original OHLC open in quote price units. |
| high | float | input | Original OHLC high in quote price units. |
| low | float | input | Original OHLC low in quote price units. |
| close | float | input | Original OHLC close in quote price units. |
| tick_volume | int | input | Broker tick volume; not centralized FX traded volume. |
| real_volume | float | input_optional | Native real_volume when present. Preserve native zero; consult event availability flag. |
| bar_spread_points | int | input_optional | Native MqlRates spread field in points. Not a full intrabar spread path or guaranteed fill spread. |
| gap_before_seconds | int | audit | max(0, open_time minus prior open_time minus timeframe seconds). Includes scheduled closures. |
| gap_class | enum:none/scheduled_close/unexplained/unknown | audit | Scheduled classification only with verified trading-session calendar. |


## history/M15.csv

| Column | Type | Role | Meaning |
| --- | --- | --- | --- |
| source_symbol | string | context | source_symbol |
| pair | string | context | pair |
| timeframe | enum:M1/M5/M15 | context | Timeframe of this row. |
| open_time_server | datetime | context | Original broker chart time; ISO 8601 without an invented UTC offset. |
| close_time_server | datetime | context | Exclusive bar boundary: open time plus timeframe duration. Not last tick time. |
| open | float | input | Original OHLC open in quote price units. |
| high | float | input | Original OHLC high in quote price units. |
| low | float | input | Original OHLC low in quote price units. |
| close | float | input | Original OHLC close in quote price units. |
| tick_volume | int | input | Broker tick volume; not centralized FX traded volume. |
| real_volume | float | input_optional | Native real_volume when present. Preserve native zero; consult event availability flag. |
| bar_spread_points | int | input_optional | Native MqlRates spread field in points. Not a full intrabar spread path or guaranteed fill spread. |
| gap_before_seconds | int | audit | max(0, open_time minus prior open_time minus timeframe seconds). Includes scheduled closures. |
| gap_class | enum:none/scheduled_close/unexplained/unknown | audit | Scheduled classification only with verified trading-session calendar. |


## manifest.csv

| Column | Type | Role | Meaning |
| --- | --- | --- | --- |
| event_id | string | join_key | Unique across broker, source symbol, decision time and setup sequence. |
| pair | enum:EURUSD/USDJPY/GBPUSD | context | Normalized folder pair. |
| source_symbol | string | context | Exact broker symbol, including suffix, e.g. EURUSD#. |
| broker_server | string | context | Broker server name. No account number or credentials. |
| decision_time_server | datetime | context | Information cutoff shared by every timeframe and all pre-entry fields. |
| overlap_group_id | string | split_control | Group nearby/overlapping events. Not sufficient by itself to ensure time-purged splits. |
| split | enum:unassigned/train/validation/test/excluded | split_control | Assigned globally by time across pairs after collecting windows and label horizons. |
| quality_status | enum:valid/insufficient_history/missing_data/time_unverified/needs_review | audit | Data eligibility for the intended training task. |
| relative_event_path | string | audit | Path relative to dataset root. |
| input_start_server | datetime | split_control | Earliest input bar open across all timeframes. |
| input_end_server | datetime | split_control | Latest fully closed input bar boundary, <= decision. |
| label_end_server | datetime | split_control | Final outcome horizon used for temporal purging. |
| m1_rows | int | audit | Actual exported M1 input row count. |
| m5_rows | int | audit | Actual exported M5 input row count. |
| m15_rows | int | audit | Actual exported M15 input row count. |
| unexplained_gap_count | int | audit | Unexplained gaps across event inputs. |
| real_ticks_status | enum:complete/partial/unavailable/not_requested | audit | Observed tick availability, not an assumption. |
| event_content_sha256 | string | audit | Hash of deterministically ordered event CSV filenames and bytes, excluding root manifest. |
| pattern_label | string | target | pattern_label copied from the event metadata/plan/outcome; not a default model input. |
| side_label | string | target | side_label copied from the event metadata/plan/outcome; not a default model input. |
| plan_status | string | audit | plan_status copied from the event metadata/plan/outcome; not a default model input. |
| outcome_label_status | string | audit | outcome_label_status copied from the event metadata/plan/outcome; not a default model input. |
