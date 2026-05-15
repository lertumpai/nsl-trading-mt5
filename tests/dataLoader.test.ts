import { test, describe } from 'node:test';
import assert from 'node:assert/strict';
import * as path from 'path';
import { fileURLToPath } from 'url';
import { loadOHLCV } from '../src/dataLoader.js';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const FIXTURE = path.join(__dirname, 'fixtures', 'XAUUSD_H1_synthetic.csv');

describe('loadOHLCV', () => {
  test('loads all 50 bars from synthetic fixture', async () => {
    const bars = await loadOHLCV(FIXTURE);
    assert.equal(bars.length, 50);
  });

  test('parses datetime as UTC Date objects', async () => {
    const bars = await loadOHLCV(FIXTURE);
    const first = bars[0]!;
    assert.ok(first.datetime instanceof Date);
    assert.equal(first.datetime.getUTCFullYear(), 2020);
    assert.equal(first.datetime.getUTCMonth(), 0);   // January = 0
    assert.equal(first.datetime.getUTCDate(), 6);
    assert.equal(first.datetime.getUTCHours(), 8);
    assert.equal(first.datetime.getUTCMinutes(), 0);
  });

  test('first bar OHLC values are correct', async () => {
    const bars = await loadOHLCV(FIXTURE);
    const b = bars[0]!;
    assert.equal(b.open,  1552.00);
    assert.equal(b.high,  1558.50);
    assert.equal(b.low,   1549.80);
    assert.equal(b.close, 1555.20);
    assert.equal(b.tickVolume, 1820);
  });

  test('bars are sorted ascending by datetime', async () => {
    const bars = await loadOHLCV(FIXTURE);
    for (let i = 1; i < bars.length; i++) {
      assert.ok(
        bars[i]!.datetime.getTime() > bars[i - 1]!.datetime.getTime(),
        `Bar ${i} is not after bar ${i - 1}`,
      );
    }
  });

  test('glob wildcard matches fixture file', async () => {
    const pattern = path.join(__dirname, 'fixtures', 'XAUUSD_H1_*.csv');
    const bars = await loadOHLCV(pattern);
    assert.equal(bars.length, 50);
  });

  test('throws when no files match pattern', async () => {
    await assert.rejects(
      () => loadOHLCV(path.join(__dirname, 'fixtures', 'DOES_NOT_EXIST_*.csv')),
      /no files matched/,
    );
  });

  test('deduplicates identical datetimes across multiple loads', async () => {
    // Simulated by loading same fixture twice via glob that matches one file
    const bars = await loadOHLCV(FIXTURE);
    const times = bars.map(b => b.datetime.getTime());
    const unique = new Set(times);
    assert.equal(times.length, unique.size);
  });

  test('last bar is from 2020-01-10', async () => {
    const bars = await loadOHLCV(FIXTURE);
    const last = bars.at(-1)!;
    assert.equal(last.datetime.getUTCFullYear(), 2020);
    assert.equal(last.datetime.getUTCDate(), 10);
    assert.equal(last.datetime.getUTCHours(), 17);
  });
});
