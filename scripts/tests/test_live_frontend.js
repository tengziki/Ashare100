// 直接执行：node scripts/tests/test_live_frontend.js
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const web = path.resolve(__dirname, '../../ashare100/ashare100/web');
const manifest = JSON.parse(fs.readFileSync(path.join(web, 'live/manifest.json'), 'utf8'));
const snapshot = JSON.parse(fs.readFileSync(path.join(web, 'live', manifest.snapshot), 'utf8'));
const code = fs.readFileSync(path.join(web, 'live-data.js'), 'utf8');
const data = fs.readFileSync(path.join(web, 'data.js'), 'utf8');

async function run(fetchImpl, protocol = 'http:') {
  const context = { console: { warn() {} }, location: { protocol, reload() { context.reloaded = true; } },
    document: { hidden: false }, fetch: fetchImpl };
  context.window = context;
  vm.createContext(context);
  vm.runInContext(data, context);
  const original = context.__DATA__;
  vm.runInContext(code, context);
  await context.__LIVE_READY__;
  return { context, original };
}

(async () => {
  const fetchOk = async (url) => ({ ok: true, json: async () =>
    url.endsWith('manifest.json') ? manifest : snapshot });
  const { context, original } = await run(fetchOk);
  const current = context.__DATA__;
  assert.equal(context.__LIVE_STATE__.mode, 'live');
  assert.equal(Object.keys(current.companies).length, 100);
  assert.deepEqual(Object.keys(current.companies).sort(), snapshot.members.map((r) => r.code).sort());
  assert.equal(current.meta.live.quote_date, snapshot.meta.quote_date);
  assert.equal(current.companies[snapshot.members[0].code].market.total_mv, snapshot.members[0].total_mv);
  assert.equal(current.companies['600016.SH'].profile_status, 'pending');
  assert.equal(current.companies['600016.SH'].shareholders.inst_hold_ratio, null);
  assert.equal(current.companies['600016.SH'].financials.annual.length, 5);
  assert.equal(current.companies['600519.SH'].financials, original.companies['600519.SH'].financials);
  assert.ok(!current.companies['300033.SZ']);
  context.fetch = async (url) => ({ ok: true, json: async () =>
    url.endsWith('manifest.json') ? { ...manifest, snapshot: 'snapshots/new-version.json' } : snapshot });
  await context.__LIVE_CHECK__();
  assert.equal(context.reloaded, true);

  const badFetch = async () => { throw new Error('network failed'); };
  const fallback = await run(badFetch);
  assert.equal(fallback.context.__LIVE_STATE__.mode, 'fallback');
  assert.equal(fallback.context.__DATA__, fallback.original);
  fallback.context.fetch = fetchOk;
  await fallback.context.__LIVE_CHECK__();
  assert.equal(fallback.context.reloaded, true);
  const offline = await run(badFetch, 'file:');
  assert.equal(offline.context.__LIVE_STATE__.mode, 'offline');
  assert.equal(offline.context.__DATA__, offline.original);
  console.log('live frontend data contract: OK');
})().catch((error) => { console.error(error); process.exitCode = 1; });
