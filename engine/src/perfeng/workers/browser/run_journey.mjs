// Frontend performance worker: runs ONE persisted deterministic Playwright journey in a real browser,
// collects Core Web Vitals + lab metrics + evidence, then (optionally) a Lighthouse navigation audit.
// No LLM is involved at run time.
//
//   node run_journey.mjs --journey <file.spec.ts> --name <Journey> --base-url <url> --out <dir>
//                        [--lighthouse-runs N] [--cpu-slowdown X] [--viewport 1350x940]
//
// The journey file exports `async function journey(page, ctx)` and may also contain a normal
// Playwright Test `test(...)` so developers can run it with `npx playwright test`.
import { chromium } from 'playwright';
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { pathToFileURL, fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const pkgVersion = (name) => { try { return JSON.parse(fs.readFileSync(path.join(HERE, 'node_modules', name, 'package.json'), 'utf8')).version; } catch { return null; } };

function args() {
  const a = process.argv.slice(2);
  const o = { lighthouseRuns: 1, cpuSlowdown: 1, viewport: '1350x940' };
  for (let i = 0; i < a.length; i += 2) {
    const k = a[i].replace(/^--/, '').replace(/-([a-z])/g, (_, c) => c.toUpperCase());
    o[k] = a[i + 1];
  }
  o.lighthouseRuns = Number(o.lighthouseRuns);
  o.cpuSlowdown = Number(o.cpuSlowdown);
  return o;
}

const VITALS_INIT = `
(() => {
  window.__perf = { vitals: {}, longtasks: [], errors: [] };
  try {
    new PerformanceObserver((l) => { for (const e of l.getEntries()) window.__perf.longtasks.push({ start: e.startTime, duration: e.duration }); })
      .observe({ type: 'longtask', buffered: true });
  } catch (e) {}
  const report = (m) => { window.__perf.vitals[m.name] = { value: m.value, rating: m.rating, entries: m.entries ? m.entries.length : 0 }; };
  // NB: Playwright may wrap init scripts in a function, so reference the binding, not window.webVitals
  if (typeof webVitals === 'undefined') return;
  const o = { reportAllChanges: true };
  webVitals.onLCP(report, o); webVitals.onCLS(report, o); webVitals.onINP(report, o);
  webVitals.onFCP(report, o); webVitals.onTTFB(report, o);
})();`;

async function collectPage(page) {
  // flush LCP/CLS/INP: web-vitals reports final values when the page is hidden
  await page.evaluate(() => {
    Object.defineProperty(document, 'visibilityState', { value: 'hidden', configurable: true });
    document.dispatchEvent(new Event('visibilitychange'));
  }).catch(() => {});
  await page.waitForTimeout(150);
  return page.evaluate(() => {
    const nav = performance.getEntriesByType('navigation')[0];
    const paints = Object.fromEntries(performance.getEntriesByType('paint').map((p) => [p.name, p.startTime]));
    const res = performance.getEntriesByType('resource');
    return {
      vitals: (window.__perf || {}).vitals || {},
      longtasks: (window.__perf || {}).longtasks || [],
      ttfb: nav ? nav.responseStart - nav.startTime : null,
      fcp: paints['first-contentful-paint'] ?? null,
      domContentLoaded: nav ? nav.domContentLoadedEventEnd : null,
      load: nav ? nav.loadEventEnd : null,
      resources: res.map((r) => ({ name: r.name, type: r.initiatorType, start: r.startTime, duration: r.duration,
        transfer: r.transferSize, encoded: r.encodedBodySize })),
    };
  });
}

function lighthouse(url, chromePath, outDir, i) {
  const bin = path.join(HERE, 'node_modules', 'lighthouse', 'cli', 'index.js');
  const out = path.join(outDir, `lighthouse-${i}.json`);
  const r = spawnSync(process.execPath, [bin, url, '--output=json', `--output-path=${out}`, '--only-categories=performance',
    '--quiet', '--chrome-flags=--headless=new --no-sandbox --disable-gpu', '--form-factor=desktop', '--screenEmulation.disabled',
    '--throttling-method=simulate'], { env: { ...process.env, CHROME_PATH: chromePath }, encoding: 'utf8', timeout: 180000 });
  if (!fs.existsSync(out)) return { error: (r.stderr || r.stdout || 'lighthouse failed').slice(-800) };
  const lhr = JSON.parse(fs.readFileSync(out, 'utf8'));
  const a = lhr.audits;
  const n = (k) => (a[k] && typeof a[k].numericValue === 'number' ? a[k].numericValue : null);
  return {
    file: path.basename(out), version: lhr.lighthouseVersion, score: lhr.categories.performance.score,
    lcp_ms: n('largest-contentful-paint'), tbt_ms: n('total-blocking-time'), cls: n('cumulative-layout-shift'),
    fcp_ms: n('first-contentful-paint'), speed_index_ms: n('speed-index'), ttfb_ms: n('server-response-time'),
    js_exec_ms: n('bootup-time'), mainthread_ms: n('mainthread-work-breakdown'),
    critical_chains: a['critical-request-chains'] ? Object.keys(a['critical-request-chains'].details?.chains || {}).length : null,
  };
}

async function main() {
  const o = args();
  fs.mkdirSync(o.out, { recursive: true });
  const journeyFile = path.resolve(o.journey);
  // copy journey next to our node_modules so `import '@playwright/test'` resolves
  const local = path.join(HERE, 'journeys', path.basename(journeyFile));
  fs.mkdirSync(path.dirname(local), { recursive: true });
  fs.copyFileSync(journeyFile, local);
  globalThis.__PERF_RUNNER__ = true;
  const mod = await import(pathToFileURL(local).href + `?t=${Date.now()}`);
  if (typeof mod.journey !== 'function') throw new Error(`${journeyFile} must export async function journey(page, ctx)`);

  const chromePath = chromium.executablePath();
  const [vw, vh] = o.viewport.split('x').map(Number);
  const browser = await chromium.launch({ headless: true });
  const browserVersion = browser.version();
  const context = await browser.newContext({ viewport: { width: vw, height: vh }, baseURL: o.baseUrl,
    recordHar: { path: path.join(o.out, 'network.har'), content: 'omit' } });
  await context.tracing.start({ screenshots: true, snapshots: true });
  const webVitalsSrc = fs.readFileSync(path.join(HERE, 'node_modules', 'web-vitals', 'dist', 'web-vitals.iife.js'), 'utf8');
  await context.addInitScript({ content: webVitalsSrc + '\n' + VITALS_INIT });
  const page = await context.newPage();
  const cdp = await context.newCDPSession(page);
  await cdp.send('Performance.enable');
  if (o.cpuSlowdown > 1) await cdp.send('Emulation.setCPUThrottlingRate', { rate: o.cpuSlowdown });

  const consoleErrors = []; const failed = []; const navigations = [];
  page.on('console', (m) => { if (m.type() === 'error') consoleErrors.push(m.text().slice(0, 300)); });
  page.on('pageerror', (e) => consoleErrors.push(String(e).slice(0, 300)));
  page.on('requestfailed', (r) => failed.push({ url: r.url(), error: r.failure()?.errorText }));
  page.on('response', (r) => { if (r.status() >= 400) failed.push({ url: r.url(), status: r.status() }); });
  page.on('framenavigated', (f) => { if (f === page.mainFrame()) navigations.push(f.url()); });

  const marks = [];
  const ctx = { baseURL: o.baseUrl, env: process.env, mark: async (name) => marks.push({ name, t: Date.now() }) };
  let journeyError = null;
  const t0 = Date.now();
  try {
    await mod.journey(page, ctx);
  } catch (e) {
    journeyError = String(e).slice(0, 1000);
  }
  const journeyMs = Date.now() - t0;
  const pm = Object.fromEntries((await cdp.send('Performance.getMetrics')).metrics.map((m) => [m.name, m.value]));
  const collected = await collectPage(page).catch((e) => ({ error: String(e) }));
  await page.screenshot({ path: path.join(o.out, 'final.png') }).catch(() => {});
  await context.tracing.stop({ path: path.join(o.out, 'trace.zip') });
  await context.close();
  await browser.close();

  const v = collected.vitals || {};
  const lts = collected.longtasks || [];
  const res = collected.resources || [];
  const metrics = {
    journey_ms: journeyMs,
    lcp_ms: v.LCP?.value ?? null,
    cls: v.CLS?.value ?? 0,
    inp_ms: v.INP?.value ?? null, // null when no qualifying interaction happened
    ttfb_ms: collected.ttfb ?? v.TTFB?.value ?? null,
    fcp_ms: collected.fcp ?? v.FCP?.value ?? null,
    long_tasks: lts.length,
    long_task_ms: lts.reduce((s, x) => s + x.duration, 0),
    tbt_ms: lts.reduce((s, x) => s + Math.max(0, x.duration - 50), 0), // lab approximation in-journey
    js_exec_ms: pm.ScriptDuration != null ? pm.ScriptDuration * 1000 : null,
    task_ms: pm.TaskDuration != null ? pm.TaskDuration * 1000 : null,
    resource_count: res.length,
    transfer_kb: res.reduce((s, r) => s + (r.transfer || 0), 0) / 1024,
    failed_requests: failed.length,
    console_errors: consoleErrors.length,
  };
  const lh = [];
  const lhUrl = mod.lighthouseUrl ? new URL(mod.lighthouseUrl, o.baseUrl).href : (navigations[0] || o.baseUrl);
  for (let i = 0; i < o.lighthouseRuns; i++) lh.push(lighthouse(lhUrl, chromePath, o.out, i));
  const okLh = lh.filter((x) => !x.error);
  if (okLh.length) {
    const med = (k) => { const a = okLh.map((x) => x[k]).filter((x) => x != null).sort((p, q) => p - q); return a.length ? a[Math.floor(a.length / 2)] : null; };
    metrics.speed_index_ms = med('speed_index_ms');
    metrics.lh_tbt_ms = med('tbt_ms');
    metrics.lh_lcp_ms = med('lcp_ms');
    metrics.lh_performance_score = med('score');
  }
  const result = {
    journey: o.name, ok: !journeyError, error: journeyError, metrics,
    evidence: { trace: 'trace.zip', har: 'network.har', screenshot: 'final.png', lighthouse: lh, console_errors: consoleErrors.slice(0, 50),
      failed_requests: failed.slice(0, 50), longtasks: lts.slice(0, 100), marks, navigations,
      slowest_resources: [...res].sort((a, b) => b.duration - a.duration).slice(0, 15), cdp: pm },
    versions: { playwright: pkgVersion('playwright'), chromium: browserVersion,
      lighthouse: okLh[0]?.version ?? null, 'web-vitals': pkgVersion('web-vitals') },
  };
  fs.writeFileSync(path.join(o.out, 'journey-result.json'), JSON.stringify(result, null, 2));
  process.stdout.write(JSON.stringify({ ok: result.ok, error: journeyError }) + '\n');
  process.exit(journeyError ? 3 : 0);
}

main().catch((e) => { console.error(e); process.exit(1); });
