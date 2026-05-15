import { createReadStream, readdirSync } from 'fs';
import { parse } from 'csv-parse';
import * as path from 'path';
import type { Bar } from './types.js';

/**
 * Expand a glob-like pattern that may contain a single `*` in the filename.
 * Handles paths like `data/raw/XAUUSD/XAUUSD_H1_*.csv`.
 * Returns sorted list of matching absolute/relative file paths.
 */
function expandGlob(pattern: string): string[] {
  const dir      = path.dirname(pattern);
  const basename = path.basename(pattern);

  if (!basename.includes('*')) return [pattern];

  const regexStr = '^' + basename.replace(/\./g, '\\.').replace(/\*/g, '.*') + '$';
  const regex    = new RegExp(regexStr);

  let files: string[];
  try {
    files = readdirSync(dir);
  } catch {
    return [];
  }

  return files
    .filter(f => regex.test(f))
    .sort()
    .map(f => path.join(dir, f));
}

/**
 * Load one or more XAUUSD CSV files.
 * Accepts an exact path or a glob pattern (single `*` in filename).
 * Supported MT5 export formats:
 *   - Python MetaTrader5 lib: time,open,high,low,close,tick_volume,...
 *   - MT5 History Center:     <DATE>,<TIME>,<OPEN>,<HIGH>,<LOW>,<CLOSE>,<TICKVOL>,...
 *   - MT5 MQL5 script:        datetime,open,high,low,close,tick_volume
 */
export async function loadOHLCV(filePattern: string): Promise<Bar[]> {
  const files = expandGlob(filePattern);

  if (files.length === 0) {
    throw new Error(`loadOHLCV: no files matched "${filePattern}"`);
  }

  const allBars: Bar[] = [];
  for (const filePath of files) {
    const bars = await loadSingleFile(filePath);
    allBars.push(...bars);
  }

  // Deduplicate and sort ascending by time
  const seen  = new Map<number, Bar>();
  for (const b of allBars) seen.set(b.datetime.getTime(), b);

  return [...seen.values()].sort((a, b) => a.datetime.getTime() - b.datetime.getTime());
}

async function loadSingleFile(filePath: string): Promise<Bar[]> {
  return new Promise((resolve, reject) => {
    const bars: Bar[] = [];

    createReadStream(filePath)
      .pipe(parse({ columns: true, trim: true, skip_empty_lines: true }))
      .on('data', (row: Record<string, string>) => {
        try {
          const bar = parseRow(row, filePath);
          if (bar !== null) bars.push(bar);
        } catch {
          // Row is malformed — skip and continue (caller should run validate())
        }
      })
      .on('end', () => resolve(bars))
      .on('error', reject);
  });
}

function parseRow(row: Record<string, string>, filePath: string): Bar | null {
  // --- Detect column format ---
  const rawDatetime =
    row['datetime'] ?? row['time'] ??
    (row['<DATE>'] && row['<TIME>'] ? `${row['<DATE>']} ${row['<TIME>']}` : undefined);

  if (!rawDatetime) throw new Error(`No datetime column in ${filePath}`);

  const open  = parseFloat(row['open']  ?? row['<OPEN>']  ?? '');
  const high  = parseFloat(row['high']  ?? row['<HIGH>']  ?? '');
  const low   = parseFloat(row['low']   ?? row['<LOW>']   ?? '');
  const close = parseFloat(row['close'] ?? row['<CLOSE>'] ?? '');
  const vol   = parseFloat(row['tick_volume'] ?? row['<TICKVOL>'] ?? row['<VOL>'] ?? '0');

  // Reject rows where core price fields are invalid
  if (isNaN(open) || isNaN(high) || isNaN(low) || isNaN(close)) return null;

  // --- Parse datetime to UTC ---
  const datetime = parseDatetimeUTC(rawDatetime);
  if (datetime === null) return null;

  return { datetime, open, high, low, close, tickVolume: isNaN(vol) ? 0 : vol };
}

/**
 * Parse MT5-exported datetime strings to a UTC Date.
 * MT5 typically exports in broker server time (often UTC+2 or UTC+3).
 * This function treats the timestamp as UTC unless a Z/offset is already present.
 * If your broker's server time differs from UTC, apply the offset before loading.
 */
function parseDatetimeUTC(raw: string): Date | null {
  const trimmed = raw.trim();

  // Already has timezone info
  if (trimmed.endsWith('Z') || /[+-]\d{2}:\d{2}$/.test(trimmed)) {
    const d = new Date(trimmed);
    return isNaN(d.getTime()) ? null : d;
  }

  // ISO-style without timezone: "2015-01-05 08:00:00" or "2015.01.05 08:00"
  // Normalise separators and treat as UTC
  const normalised = trimmed
    .replace(/\./g, '-')      // 2015.01.05 → 2015-01-05
    .replace(' ', 'T')        // space → T separator
    + 'Z';                    // append Z to force UTC

  const d = new Date(normalised);
  return isNaN(d.getTime()) ? null : d;
}
