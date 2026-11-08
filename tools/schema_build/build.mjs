import fs from 'node:fs/promises';
import path from 'node:path';
import {Workbook} from '@oai/artifact-tool';

const out=path.resolve('dataset/schema_v1');
await fs.mkdir(path.join(out,'event_template'),{recursive:true});
// Each field: name, type, role, required, meaning. Targets never enter the input feature allowlist.
const f=(name,type,role,meaning,required=true)=>({name,type,role,required,meaning});
const id=f('event_id','string','join_key','Unique across broker, source symbol, decision time and setup sequence.');
const barFields=[id,
 f('timeframe','enum:M1|M5|M15','context','Timeframe of this row.'),
 f('bar_index','int','context','Latest fully closed bar is 0; earlier bars are negative. Rows are oldest to newest.'),
 f('open_time_server','datetime','context','Original broker chart time; ISO 8601 without an invented UTC offset.'),
 f('close_time_server','datetime','context','Exclusive bar boundary: open time plus timeframe duration. Not last tick time.'),
 f('open_time_utc','datetime_utc','context','UTC conversion only when the historical broker offset is verified.',false),
 f('open','float','input','Original OHLC open in quote price units.'),
 f('high','float','input','Original OHLC high in quote price units.'),
 f('low','float','input','Original OHLC low in quote price units.'),
 f('close','float','input','Original OHLC close in quote price units.'),
 f('tick_volume','int','input','Broker tick volume; not centralized FX traded volume.'),
 f('real_volume','float','input_optional','Native real_volume when present. Preserve native zero; consult event availability flag.',false),
 f('bar_spread_points','int','input_optional','Native MqlRates spread field in points. Not a full intrabar spread path or guaranteed fill spread.',false),
 f('source_m1_count','int','audit','For aggregated bars: count of source M1 rows. Missing where native aggregation cannot be independently verified.',false),
 f('gap_before_seconds','int','audit','max(0, open_time minus prior open_time minus timeframe seconds). Includes scheduled closures.'),
 f('gap_class','enum:none|scheduled_close|unexplained|unknown','audit','Scheduled classification only with verified trading-session calendar.'),
 f('feature_valid','bool','audit','1 only when the required feature history and quality conditions are satisfied.'),
 f('body_price','float','input','abs(close-open), in price units.'),
 f('upper_wick_price','float','input','high-max(open,close), in price units.'),
 f('lower_wick_price','float','input','min(open,close)-low, in price units.'),
 f('body_to_range','float','input','abs(close-open)/(high-low); blank for zero-range bars.',false),
 f('return_1bar','float','input','close/prior_close-1; blank if unavailable. Gaps are retained and flagged.',false),
 f('atr_14_price','float','input_optional','Causal ATR(14) in price units, definition fixed in schema configuration.',false),
 f('range_atr_14','float','input_optional','(high-low)/atr_14_price; blank when ATR missing or zero.',false),
 f('ema_9_price','float','input_optional','Causal EMA(9) of close; never initialize afresh at each event window.',false),
 f('ema_21_price','float','input_optional','Causal EMA(21) of close.',false),
 f('ema_200_price','float','input_optional','Causal EMA(200) of close.',false),
 f('rsi_14','float','input_optional','Causal Wilder RSI(14) of close.',false),
 f('tick_volume_ratio_20','float','input_optional','Current tick_volume / mean of preceding 20 completed bars, excluding current from denominator.',false),
];
const eventFields=[id,
 f('schema_version','string','audit','Dataset contract version.'),
 f('pair','enum:EURUSD|USDJPY|GBPUSD','context','Normalized folder pair.'),
 f('source_symbol','string','context','Exact broker symbol, including suffix, e.g. EURUSD#.'),
 f('broker_server','string','context','Broker server name. No account number or credentials.'),
 f('decision_time_server','datetime','context','Information cutoff shared by every timeframe and all pre-entry fields.'),
 f('decision_time_utc','datetime_utc','context','Verified UTC equivalent, otherwise blank.',false),
 f('server_utc_offset_minutes','int','context','Historical offset at this event, not the current computer offset.',false),
 f('time_mapping_source','string','audit','Evidence/version for historical time conversion or unverified.'),
 f('extracted_at_utc','datetime_utc','audit','Actual export timestamp in UTC.'),
 f('price_basis','enum:bid|ask|last|unknown','context','Verified source basis of OHLC.'),
 f('digits','int','context','Broker display precision at the event if verified.'),
 f('point_size','float','context','Price units per broker point, from verified symbol metadata.'),
 f('pip_size','float','context','Price units per pip. Default convention 0.0001 for EURUSD/GBPUSD, 0.01 for USDJPY; verify.'),
 f('real_volume_status','enum:available|not_provided|unverified','audit','Interpret native real_volume zero using broker availability.'),
 f('quote_bid_at_decision','float','input_optional','Last observed Bid tick at or before the decision, never a future quote.',false),
 f('quote_ask_at_decision','float','input_optional','Last observed Ask tick at or before the decision.',false),
 f('quote_time_server','datetime','audit','Time of the preceding quote, for quote staleness checks.',false),
 f('selection_rule_version','string','audit','Immutable setup/selection configuration version.'),
 f('feature_rule_version','string','audit','Immutable feature calculation configuration version.'),
 f('selection_mode','enum:causal_rule|asof_blind_manual|retrospective_manual','audit','Retrospective manual examples are separated from causal training/evaluation.'),
 f('selection_population_id','string','audit','Scan/control sampling population identifier, linked to a stored extraction configuration.'),
 f('sampling_probability','float','audit','Inclusion probability in (0,1] for sampled controls; 1 for an exhaustive deterministic rule scan. Unknown stays blank.',false),
 f('pattern_label','enum:trend_pullback|breakout_retest|sweep_reclaim|range_rejection|no_setup|unclear','target','Price-action class assigned without seeing future performance.'),
 f('side_label','enum:buy|sell|no_trade|unlabeled','target','Intended decision label, not a default model input.'),
 f('review_status','enum:unreviewed|accepted|rejected|unclear','audit','Review quality, independent of win or loss.'),
 f('selection_reason','string','audit','Concrete visible structure/trigger, not future profitability.'),
 f('reviewer_id','string','audit','Reviewer identifier or rule engine version.',false),
 f('overlap_group_id','string','split_control','Group nearby/overlapping events. Not sufficient by itself to ensure time-purged splits.'),
 f('split','enum:unassigned|train|validation|test|excluded','split_control','Assigned globally by time across pairs after collecting windows and label horizons.'),
 f('quality_status','enum:valid|insufficient_history|missing_data|time_unverified|needs_review','audit','Data eligibility for the intended training task.'),
 f('quality_notes','string','audit','Specific missing data, ambiguity or exclusions.',false),
 f('news_context_status','enum:verified|unavailable|not_collected','audit','Do not infer absence of news from missing historical calendar data.'),
 f('minutes_to_next_scheduled_high_impact_event','float','input_optional','Only historical scheduled information known at decision time; never released actual/surprise data.',false),
];
const planFields=[id,
 f('plan_id','string','join_key','One primary entry/exit policy per event in v1.'),
 f('entry_rule_version','string','audit','Versioned executable entry rule or draft_manual.'),
 f('exit_rule_version','string','audit','Versioned exit rule including priority and time stop.'),
 f('plan_status','enum:complete|incomplete|no_trade','audit','A complete trade has explicit executable entry, SL, TP and time/cancel rules.'),
 f('side','enum:buy|sell|no_trade','target','Planned side for supervised training.'),
 f('entry_order_type','enum:market_next_tick|stop|limit|none','target','Market orders use first eligible executable quote after decision; never guaranteed signal close fills.'),
 f('trigger_time_server','datetime','target','Time at which entry trigger became observable, no later than decision.',false),
 f('entry_reference_price','float','target','Known decision-time order level/reference; next market fill remains unknown.',false),
 f('entry_price_basis','enum:ask|bid|none','audit','Buy opens at Ask; sell opens at Bid in quote-based simulation.'),
 f('entry_condition','string','target','Precise observable trigger, linked to known level IDs.'),
 f('entry_expiry_server','datetime','target','Unfilled entry becomes canceled after this time.',false),
 f('cancel_condition','string','target','Pre-entry invalidation rule, including structure failure and data/quote constraints.'),
 f('max_spread_points','float','target','Decision/order-time spread constraint, versioned before evaluation.',false),
 f('stop_loss_price','float','target','Initial executable stop level; must be valid for planned side.',false),
 f('stop_basis_level_id','string','target','Confirmed structure level that fixes invalidation, if used.',false),
 f('stop_buffer_points','float','target','Explicit buffer in broker points, fixed from pre-entry policy.',false),
 f('take_profit_price','float','target','Primary pre-entry target; no future swing-extreme chosen after the fact.',false),
 f('target_basis_level_id','string','target','Previously known opposing structure level, if used.',false),
 f('target_policy','string','target','Exact target method. Any fixed distance/ATR multiplier is specified here.'),
 f('max_hold_seconds','int','target','Time stop measured from actual fill.'),
 f('early_exit_condition','string','target','Observable early invalidation or none.'),
 f('trailing_policy','string','target','none in v1 unless a fully specified rule is registered.'),
 f('breakeven_policy','string','target','none in v1 unless a fully specified rule is registered.'),
 f('estimated_commission_pips_roundtrip','float','audit','Known broker commission estimate converted to pips, not assumed zero.',false),
 f('slippage_assumption_points','float','audit','Scenario assumption, not historical observed slippage.',false),
 f('execution_delay_ms','int','audit','Explicit backtest delay assumption.',false),
];
const levelFields=[id,
 f('level_id','string','join_key','Unique within event.'),
 f('timeframe','enum:M1|M5|M15|D1','context','Origin timeframe. D1 is allowed only for known previous-day levels.'),
 f('level_type','enum:swing_high|swing_low|prior_day_high|prior_day_low|range_high|range_low|breakout_zone','input_optional','Price-action structure known by cutoff.'),
 f('price_low','float','input_optional','Lower edge of price level/zone.'),
 f('price_high','float','input_optional','Upper edge; equals price_low for a point level.'),
 f('origin_time_server','datetime','audit','Origin bar or start of underlying structure.'),
 f('known_at_server','datetime','context','Earliest time at which confirmation was possible. Must be <= decision.'),
 f('detection_rule_version','string','audit','Exact swing/zone algorithm version.'),
 f('left_bars','int','audit','Swing parameter, if applicable.',false),
 f('right_confirmation_bars','int','audit','Right-hand confirming bars already closed by known_at; never use them earlier.',false),
 f('touch_count_asof','int','input_optional','Number of contacts known by cutoff.',false),
 f('status_asof','enum:active|broken|reclaimed','input_optional','Level state using only observations by cutoff.'),
];
const outcomeFields=[id,
 f('plan_id','string','join_key','Matches the versioned primary plan.'),
 f('outcome_source','enum:simulated_real_ticks|simulated_bars|actual_deals|not_evaluated','audit','Do not describe simulated fills as real trades.'),
 f('label_status','enum:complete|unfilled|canceled|ambiguous|censored|missing_ticks|not_evaluated','audit','No forced win/loss when path or horizon is unresolved.'),
 f('entry_fill_time_server','datetime','future_label','Actual or simulated fill time.',false),
 f('entry_fill_price','float','future_label','Executable fill including modeled slippage when applicable.',false),
 f('exit_time_server','datetime','future_label','Actual or simulated exit time.',false),
 f('exit_fill_price','float','future_label','Buy liquidates on Bid; sell on Ask, subject to execution model.',false),
 f('exit_reason','enum:sl|tp|time_stop|invalidation|unfilled|canceled|unknown','future_label','Terminal event under the registered exit policy.'),
 f('holding_seconds','int','future_label','Elapsed fill-to-exit duration.',false),
 f('initial_risk_pips','float','future_label','abs(entry_fill_price-initial_stop_loss_price)/pip_size, fixed at fill.',false),
 f('gross_pnl_pips','float','future_label','side_sign*(exit_fill_price-entry_fill_price)/pip_size. Bid/Ask spread and fill slippage already embedded.',false),
 f('commission_pips_roundtrip','float','future_label','Additional round-trip commission in pips; blank if not established.',false),
 f('financing_cost_pips','float','future_label','Additional financing cost in pips; positive costs reduce net, negative credits increase net.',false),
 f('net_pnl_pips','float','future_label','gross_pnl_pips-commission_pips_roundtrip-financing_cost_pips. Do not subtract spread/slippage a second time.',false),
 f('net_r','float','future_label','net_pnl_pips/initial_risk_pips; blank if costs or risk missing.',false),
 f('mfe_pips','float','future_label','Maximum nonnegative favorable executable liquidation excursion from fill until exit.',false),
 f('mae_pips','float','future_label','Maximum nonnegative adverse executable liquidation excursion from fill until exit.',false),
 f('mfe_r','float','future_label','mfe_pips/initial_risk_pips.',false),
 f('mae_r','float','future_label','mae_pips/initial_risk_pips.',false),
 f('same_bar_sl_tp','bool','audit','1 if M1 bar data alone permits both SL and TP; tick order required to resolve.',false),
 f('real_tick_coverage_ratio','float','audit','Measured coverage of required tradable minutes by real ticks, with denominator defined in config.',false),
 f('label_end_time_server','datetime','split_control','End of observed outcome or censoring horizon, used for purging.',false),
 f('evaluation_rule_version','string','audit','Exact simulator/execution and label implementation version.'),
];
const tickFields=[id,
 f('time_msc_raw','int64','audit','Native MT5 tick time_msc preserved without reinterpretation.'),
 f('time_server','datetime','audit','Tick chart-server time after verified source-time mapping; blank while mapping unresolved.',false),
 f('time_utc_msc','int64','audit','Verified UTC timestamp only if mapping established.',false),
 f('sequence','int','audit','Original order within an equal-millisecond timestamp.'),
 f('bid','float','audit','Broker observed Bid.'),
 f('ask','float','audit','Broker observed Ask.'),
 f('last','float','audit','Native Last, if meaningful for source.',false),
 f('flags','int','audit','Native MT5 tick flags.'),
 f('spread_points','float','audit','(ask-bid)/point_size when both quotes valid.'),
 f('phase','enum:pre_decision|post_decision','split_control','Post-decision ticks never enter decision-time features.'),
 f('source','enum:broker_real|synthetic','audit','Synthetic observations never presented as broker real ticks.'),
];
const manifestFields=[id,
 ...['pair','source_symbol','broker_server','decision_time_server','overlap_group_id','split','quality_status'].map(n=>eventFields.find(x=>x.name===n)),
 f('relative_event_path','string','audit','Path relative to dataset root.'),
 f('input_start_server','datetime','split_control','Earliest input bar open across all timeframes.'),
 f('input_end_server','datetime','split_control','Latest fully closed input bar boundary, <= decision.'),
 f('label_end_server','datetime','split_control','Final outcome horizon used for temporal purging.',false),
 f('m1_rows','int','audit','Actual exported M1 input row count.'),
 f('m5_rows','int','audit','Actual exported M5 input row count.'),
 f('m15_rows','int','audit','Actual exported M15 input row count.'),
 f('unexplained_gap_count','int','audit','Unexplained gaps across event inputs.'),
 f('real_ticks_status','enum:complete|partial|unavailable|not_requested','audit','Observed tick availability, not an assumption.'),
 f('event_content_sha256','string','audit','Hash of deterministically ordered event CSV filenames and bytes, excluding root manifest.'),
];
const patterns=[
 ['trend_pullback','M15 confirmed directional structure; M5 retracement into a level known before the retracement.','M1 confirms renewed movement in the structure direction.','Confirmed pullback invalidation plus a versioned buffer.','Previously known opposing structure or a fully specified distance policy.','draft; thresholds and entry/expiry rules must be versioned before extraction'],
 ['breakout_retest','M5 closes beyond a previously confirmed level; a later retest occurs.','M1 rejects the broken level and confirms in breakout direction.','Opposite side of the retest structure plus a versioned buffer.','Previously known next opposing level or registered exit policy.','draft; exclude same-bar assumptions about breakout/retest order'],
 ['sweep_reclaim','Price crosses a known swing/prior-day boundary and closes back through it.','M1 confirms the reclaim direction after the sweep has been observed.','Beyond the observed sweep extreme plus a versioned buffer.','Known interior/opposing level or registered exit policy.','draft; crossing a level does not prove actual liquidity or stop orders'],
 ['range_rejection','A range is established from earlier completed structure; price reaches a fixed boundary.','M1 rejection/confirmation from the boundary; no confirmed breakout invalidation.','Outside the fixed range boundary/observed rejection plus buffer.','Known range interior/opposite boundary under a fixed policy.','draft; define range and boundary tolerances before scanning'],
 ['no_setup','Matched control times independent of later returns, including similar-looking failed/unclear structures.','No accepted setup or explicit no-trade condition.','No order.','No order.','Control sampling rate and probabilities must be recorded; preserve source prevalence'],
];
const tables={
 'event_template/M1.csv':{fields:barFields},'event_template/M5.csv':{fields:barFields},'event_template/M15.csv':{fields:barFields},
 'event_template/event.csv':{fields:eventFields},'event_template/plan.csv':{fields:planFields},
 'event_template/levels.csv':{fields:levelFields},'event_template/outcome.csv':{fields:outcomeFields},
 'event_template/ticks.csv':{fields:tickFields},'manifest.csv':{fields:manifestFields},
 'setup_catalog.csv':{fields:['pattern','context','trigger','stop_policy','target_policy','status'].map(n=>f(n,'string','design',n)),rows:patterns}
};
const schema={
 version:'1.0.0-draft',status:'design_only_no_market_events_exported',
 layout:'dataset/{pair}/{YYYY-MM-DD}/{HH-mm-ss}_{sequence}/',folder_time_basis:'verified broker chart decision time',
 pairs:['EURUSD','USDJPY','GBPUSD'],timeframes:['M1','M5','M15'],
 csv:{encoding:'UTF-8',delimiter:',',decimal:'.',header:true,newline:'CRLF',null:'empty field',boolean:'0 or 1',datetime:'ISO 8601; server times without invented offset; verified UTC times end with Z',formulas:'none'},
 proposed_window_rows:{M1:240,M5:288,M15:480},
 windows_note:'These are proposed input lengths, not measured coverage. Never pad missing history with invented bars. Keep actual counts and exclusions.',
 indicators:{ema_periods:[9,21,200],ema:'EMA of close; alpha=2/(n+1); seed is SMA of first n valid chronological closes. Version changes require regeneration.',rsi:'Wilder RSI(14), seed mean gains/losses of first 14 changes; both averages zero =>50; zero losses and positive gains =>100.',atr:'Wilder ATR(14): TR=max(high-low,abs(high-prev_close),abs(low-prev_close)); initial ATR is mean of first 14 valid TRs.',warmup:'Compute on prior continuous source history, not only exported event window. Prefer full prior history; minimum 1000 completed bars for exported EMA200 features; flags remain invalid when unavailable.'},
 price_action:{swing_rule_proposal:'2 closed bars left and 2 closed bars right, strict extrema; ties not pivots. Swing only available after right-side confirmation closes.',selection:'Shape/structure only, no win rate, return, RR, future MFE or outcome filter. Include accepted examples, rejected/unclear review records and matched no-setup controls.',catalog_status:'Draft qualitative definitions; exact numerical detector thresholds and entry/exit parameters are not finalized.'},
 causal_rules:[
  'Every input bar close_time_server <= event decision_time_server. Each timeframe may end at a different last-closed boundary.',
  'Only fully closed bars in v1 inputs. Do not use the final OHLC of a historically unfinished M5/M15 candle.',
  'Levels known_at_server <= decision cutoff; for per-bar level features, known_at must also be <= that row close time.',
  'Pattern and side annotations, plan targets and future outcomes are not default input features.',
  'Quote snapshots use last tick <= decision and include age; no fill at a future quote mislabeled as decision-time input.',
  'Missing calendar or timezone mappings stay unavailable. Do not mark missing news as no news.',
 ],
 execution_rules:[
  'No live orders. Collect or simulate only.',
  'Market entries occur on the first eligible executable quote after decision plus registered delay. Pending entries follow actual quote crossings.',
  'Buy entry uses Ask and exit uses Bid; sell entry uses Bid and exit uses Ask.',
  'Stop and target are fixed from information known at decision unless an explicit causal trailing rule exists.',
  'Entry expires if unfilled; time stop exits after max_hold_seconds from fill. Missing future observations cause censoring.',
  'If SL and TP are both compatible with a single bar and real ticks cannot resolve order, label ambiguous. Do not automatically assume TP first.',
  'Historical commissions and slippage are not inferred from bars. Record unknowns and explicit simulation scenarios.',
  'Spread/slippage included in executable fill prices are not subtracted again. Net results remain blank if required costs are unknown.',
 ],
 splitting:{method:'Global chronological train/validation/test across all pairs; final dates chosen after actual coverage is measured.',purge:'No overlap of full input-to-label intervals across different splits. Purge crossing events and add an explicit embargo. overlap_group_id alone is not proof.',normalization:'Fit scaler and thresholds only on training data; preserve raw prices; calculate normalized distances/returns causally.',controls:'Controls are selected without future outcomes. Record population counts and sampling probability before balancing; training-only resampling must not alter validation/test prevalence.'},
 raw_history_policy:'Keep a separate immutable per-pair monthly raw M1 archive from earliest actual available minute history through latest completed bar. Event windows are derived views; no need to duplicate the whole archive per event.',
 optional_files:['event_template/ticks.csv'],
 file_rules:{'event.csv':'Exactly one row per event.','plan.csv':'One primary plan per event; no_trade plans have blank price fields.','outcome.csv':'One row per primary plan, including not_evaluated/unfilled/censored.','levels.csv':'Zero or more confirmed levels known at decision.','ticks.csv':'Optional; may include future ticks for labels, with phase separation; never read the whole file as model input.'},
 sources:['https://www.mql5.com/en/docs/constants/structures/mqlrates','https://www.mql5.com/en/docs/series/copyrates','https://www.mql5.com/en/docs/series/copyticksrange','https://www.metatrader5.com/en/terminal/help/algotrading/tick_generation'],
 tables,
};
const dictionary=[];
for(const [file,table] of Object.entries(tables)) for(const field of table.fields) dictionary.push([file,field.name,field.type,field.role,field.required?1:0,field.meaning]);
tables['data_dictionary.csv']={fields:['file','column','type','role','required','definition'].map(n=>f(n,'string','design',n)),rows:dictionary};

const wb=Workbook.create();
const csvEscape=value=>{const s=value===null||value===undefined?'':String(value);return /[",\r\n]/.test(s)?'"'+s.replaceAll('"','""')+'"':s;};
for(const [file,table] of Object.entries(tables)){
 const names=table.fields.map(x=>x.name);
 if(new Set(names).size!==names.length) throw Error('Duplicate field in '+file);
 const sheetName=file.replace('event_template/','').replace('.csv','');
 const sh=wb.worksheets.add(sheetName);
 const values=[names,...(table.rows||[])];
 if(values.some(r=>r.length!==names.length))throw Error('Invalid matrix '+file);
 sh.getRangeByIndexes(0,0,values.length,names.length).values=values;
 sh.showGridLines=false;
 sh.getRangeByIndexes(0,0,values.length,names.length).format.font={name:'Arial',size:10};
 sh.getRangeByIndexes(0,0,1,names.length).format={fill:'#243B53',font:{name:'Arial',size:10,bold:true,color:'#FFFFFF'},rowHeight:26};
 sh.getRangeByIndexes(0,0,values.length,names.length).format.columnWidth=25;
 table._sheet=sheetName;
}
wb.recalculate();
for(const [file,table] of Object.entries(tables)){
 const sh=wb.worksheets.getItem(table._sheet);
 const vals=sh.getRangeByIndexes(0,0,1+(table.rows||[]).length,table.fields.length).values;
 await fs.writeFile(path.join(out,file),vals.map(row=>row.map(csvEscape).join(',')).join('\r\n')+'\r\n');
 // Raw CSV templates have no visual formatting. Preview only the first columns for export QA.
 const preview=await wb.render({sheetName:table._sheet,range:`A1:F${Math.min(vals.length,4)}`,scale:1,format:'png'});
 await fs.writeFile(path.resolve('tools/schema_build',table._sheet+'.png'),new Uint8Array(await preview.arrayBuffer()));
 delete table._sheet;
}
await fs.writeFile(path.join(out,'schema.json'),JSON.stringify(schema,null,2)+'\n');
await fs.writeFile(path.join(out,'DESIGN.txt'),`CSV DATASET DESIGN v1 (DRAFT)\n\nNo market event data has been exported into these templates. Files contain column headers only except the populated dictionary and setup catalog.\n\nEvent layout: dataset/PAIR/YYYY-MM-DD/HH-mm-ss_sequence/\nEach event contains M1.csv, M5.csv, M15.csv, event.csv, plan.csv, levels.csv and outcome.csv. ticks.csv is optional execution evidence.\n\nM15: wider structure/context. M5: setup formation. M1: entry trigger. Multiple timeframes are a proposed design; their incremental value should be checked in time-separated evaluation.\nProposed inputs: 240 M1 bars, 288 M5 bars, 480 M15 bars. These are research defaults and must not be confused with measured history availability. Calculate indicators using prior warmup history before slicing windows.\n\nOnly causal input columns plus approved context belong in the feature loader. Pattern, side, entry/exit plans are supervision targets. outcome.csv contains future labels. ticks.csv may include both pre/post cutoff observations and must be phase-filtered. audit, split_control, join_key and future_label fields are excluded from model inputs unless explicitly reviewed.\n\nUse price action to select setups, independently of outcomes. The catalog is a draft; exact thresholds and order/exit parameters must be registered before extraction. Human reviews should see data only up to decision time. Retrospective examples must be marked and kept separate.\n\nKeep original raw M1 history in a separate per-pair monthly archive. Do not replace missing data with zero or fabricated candles. Unexplained gaps, insufficient warmup, unverified server offsets, missing ticks and unresolved SL/TP order are explicitly flagged.\n\nHistorical commission/slippage may not be available. Preserve unknown values. Spread and fill slippage embedded in executable entry/exit prices must not be deducted twice.\n\nSplit by global chronological periods across pairs. Purge samples whose full input-to-outcome intervals overlap other splits; include an explicit embargo. Fit scalers on training only. Retain original prevalence for validation and test.\n\nSee schema.json for precise definitions, defaults, causal rules and sources.\n`);
console.log(JSON.stringify({out,csv_files:Object.keys(tables).length,dictionary_rows:dictionary.length,status:schema.status}));
