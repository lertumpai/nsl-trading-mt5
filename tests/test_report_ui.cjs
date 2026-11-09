// Offline report UI regression checks; no browser or network is used.
// Usage: node tests/test_report_ui.cjs runs/RUN_ID/report.html [...]
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
assert(process.argv.length > 2, 'Usage: node tests/test_report_ui.cjs runs/RUN_ID/report.html [...]');

function between(source, start, end) {
  const a = source.indexOf(start), b = source.indexOf(end, a);
  assert(a >= 0 && b > a, `Missing report section: ${start}`);
  return source.slice(a, b);
}

function balanced(html) {
  const stack = [], voidTags = new Set(['br', 'hr', 'input', 'img', 'meta', 'link']);
  for (const match of html.matchAll(/<(\/?)([a-z][\w-]*)\b[^>]*>/gi)) {
    const tag = match[2].toLowerCase();
    if (voidTags.has(tag)) continue;
    if (match[1]) assert.equal(stack.pop(), tag, `Unbalanced ${tag}`);
    else stack.push(tag);
  }
  assert.deepEqual(stack, []);
}

for (const path of process.argv.slice(2)) {
  const html = fs.readFileSync(path, 'utf8');
  const scripts = [...html.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/gi)].map(m => m[1]);
  scripts.forEach(script => new vm.Script(script));
  const app = scripts.find(script => script.includes('function watchBlock()'));
  assert(app, 'Missing report application');
  const report = JSON.parse(fs.readFileSync(path.replace(/report\.html$/, 'report.json'), 'utf8'));

  for (const width of [360, 1440]) for (const observer of [false, true]) {
    const calls = [], chartOptions = [], boxes = new Map();
    class Element {
      constructor(id = '') {
        this.id = id; this.dataset = {}; this.style = {}; this.children = []; this.innerHTML = '';
        this.clientWidth = width; this.clientHeight = 500;
        this.parentElement = {querySelectorAll: () => this.tabs};
        this.tabs = ['H1', 'H4', 'D1'].map(tf => ({dataset: {tf}, classList: {toggle() {}}}));
      }
      appendChild(child) { this.children.push(child); return child; }
      querySelector(selector) { return selector === '.tip' ? this.children.find(child => child.className === 'tip') : null; }
    }
    const document = {
      createElement: () => new Element(),
      getElementById: id => boxes.get(id), documentElement: {dataset: {theme: 'dark'}},
    };
    class Observer {
      constructor(callback) { this.callback = callback; }
      observe(target) { this.callback([{target, isIntersecting: true}]); }
      unobserve() {}
    }
    const window = observer ? {IntersectionObserver: Observer} : {};
    const LightweightCharts = {createChart(el, options) {
      chartOptions.push(options);
      const chart = {timeScale: () => ({setVisibleLogicalRange(range) {
        assert(range.from <= range.to);
      }}), applyOptions() {}, subscribeCrosshairMove(callback) { callback({}); }};
      chart.addCandlestickSeries = () => ({setData(data) {
        assert(data.length > 0);
        data.forEach((bar, i) => {if (i) assert(bar.time > data[i - 1].time);});
      }, attachPrimitive(primitive) { primitive.attached({chart, series: this}); },
      setMarkers() {}, applyOptions() {}, createPriceLine() {}});
      return chart;
    }};
    const context = vm.createContext({document, window, IntersectionObserver: Observer, calls,
      LightweightCharts, getComputedStyle: () => ({getPropertyValue: () => '#777777'}),
      requestAnimationFrame: callback => callback(), setTimeout: callback => callback()});
    vm.runInContext(between(app, 'const R =', '// ---------- theme ----------'), context);
    vm.runInContext(between(app, '// ---------- sections ----------', '// ---------- render page ----------'), context);

    const rendered = vm.runInContext('watchBlock()', context);
    balanced(rendered);
    assert.equal([...rendered.matchAll(/id="wchart-\d+"/g)].length, report.watch.length);
    report.watch.forEach((watch, i) => {
      assert(rendered.includes(`id="wchart-${i}" data-key="watch:${watch.symbol}"`));
      boxes.set(`wchart-${i}`, new Element(`wchart-${i}`));
    });
    assert(rendered.indexOf('class="chart"') < rendered.indexOf('class="plan-details"'));
    assert(!rendered.includes('class="side"'));

    vm.runInContext(between(app, 'const CHARTS=[];', 'plans.forEach('), context);
    vm.runInContext('const originalMakeChart=makeChart; makeChart=(el,key,plan,tf)=>{calls.push({key,tf});return originalMakeChart(el,key,plan,tf);};', context);
    vm.runInContext(app.slice(app.indexOf('const WACT=')), context);
    assert.deepEqual(calls.map(call => call.key), report.watch.map(watch => 'watch:' + watch.symbol));
    report.watch.forEach((watch, i) => {
      const box = boxes.get(`wchart-${i}`), note = box.children.find(child => child.className?.startsWith('chart-note'));
      assert.equal(box.dataset.mounted, '1');
      assert(note?.innerHTML.includes(watch.symbol));
      assert(!note.innerHTML.includes('undefined'));
      if (!watch.side_ok) assert(note.innerHTML.includes('รอทิศทางก่อนเข้า'));
      box.tabs[1].onclick();
      assert(note.innerHTML.includes('H4'));
      const before = calls.length;
      box.tabs[0].onclick();
      assert.equal(calls.length, before, 'Switching back must reuse the H1 chart');
      assert(note.innerHTML.includes('H1'));
    });
    assert(chartOptions.every(options => options.autoSize === true));
    assert(chartOptions.every(options => options.rightPriceScale.scaleMargins.top === (width < 600 ? .35 : .25)));

    // Explicitly test the proposed-plan layout even when the live run has no plan.
    context.example = {symbol: 'TEST', side: 'long', order_type: 'buy_limit', plan_id: 'TEST-ONLY',
      entry: 10, sl: 9, tp: 12, volume: 0.01, digits: 2, setup: 'A', risk_money: 1,
      risk_pct: 0.001, rr_net: 2, expiry_utc: report.generated_at + 3600,
      signal: {h4_bias: 'up', d1_state: 'support', disp_move_atr: 1.5}, invalidation: ['Test condition']};
    const plan = vm.runInContext('planCard(example, 0)', context);
    balanced(plan);
    assert(plan.indexOf('id="chart-0"') < plan.indexOf('class="plan-details"'));
    assert(plan.includes('เหตุผลและหลักฐานของแผน'));
  }
  console.log(JSON.stringify({report: path, watch_charts: report.watch.length,
    simulated_runtime_cases: 4, all_assets_mounted: true, timeframe_switching: true}));
}
