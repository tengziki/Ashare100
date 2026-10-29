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
  // 快照带自动补档时用补档档案，行情仍取当前快照；没有补档的新进公司保持 pending
  const auto = current.companies['600016.SH'];
  assert.ok(['auto_profile', 'pending', 'verified_snapshot'].includes(auto.profile_status)); // 复核转正后不再出现在 pending_profiles
  if (auto.profile_status === 'auto_profile') {
    assert.equal(auto.market.price, snapshot.members.find((r) => r.code === '600016.SH').price);
    assert.ok(Number.isFinite(auto.financials.annual[4].revenue));
    assert.equal(auto.profile_meta.errors && typeof auto.profile_meta.errors, 'object');
  }
  assert.equal(current.companies['600016.SH'].shareholders.inst_hold_ratio, null);
  assert.equal(current.companies['600016.SH'].financials.annual.length, 5);
  assert.equal(current.companies['600519.SH'].financials, original.companies['600519.SH'].financials);
  assert.equal(Boolean(current.companies['300033.SZ']), snapshot.members.some((r) => r.code === '300033.SZ')); // 成员随实际榜单变化
  // 已有档案的价格类指标按今日价换算，不能被清空
  const oldMt = original.companies['600519.SH'].market, newMt = current.companies['600519.SH'].market;
  assert.ok(Number.isFinite(newMt.ytd_return) && Number.isFinite(newMt.dividend_yield));
  assert.ok(Math.abs((1 + newMt.ytd_return / 100) / newMt.price - (1 + oldMt.ytd_return / 100) / oldMt.price) < 1e-4);
  assert.ok(newMt.high_52w >= newMt.price && newMt.low_52w <= newMt.price);
  assert.equal(newMt.return_1y, oldMt.return_1y);
  // 快照带 vs_prev_close 时直接采用；旧快照没有时，按静态收盘百强计算进出榜，退出公司带名称
  const chg = current.meta.live_changes;
  assert.match(chg.base_label, /收盘/);
  if (snapshot.changes?.vs_prev_close) {
    assert.deepEqual(chg, snapshot.changes.vs_prev_close);
  } else {
    assert.ok(chg.entered.some((r) => r.code === '600016.SH'));
    assert.ok(chg.exited.some((r) => r.code === '300033.SZ' && r.name));
  }
  const filled =Object.values(current.companies).filter((c) => c.profile_status !== 'pending');
  assert.ok(filled.filter((c) => Number.isFinite(c.market.ytd_return)).length >= filled.filter((c) => Number.isFinite(original.companies[c.identity.code + "." + c.identity.exchange]?.market.ytd_return)).length);
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
  // 日更指标：同交易日采用并按快照价换算；交易日不同则忽略
  const row = snapshot.members.find((r) => r.code === '600519.SH');
  const metricsDoc = (quoteDate) => ({ meta: { quote_date: quoteDate }, metrics: { '600519.SH': {
    price: row.price * 2, ytd_return: 10, return_1y: 20, return_3y: -30, high_52w: 1, low_52w: 1e9, dividend_yield: 2 } } });
  const withMetrics = (quoteDate) => async (url) => ({ ok: true, json: async () =>
    url.endsWith('live/manifest.json') ? manifest : url.endsWith('metrics/latest.json') ? { file: 'metrics/20261027.json' }
      : url.includes('metrics/') ? metricsDoc(quoteDate) : snapshot });
  const daily = (await run(withMetrics(snapshot.meta.quote_date))).context.__DATA__;
  const dm = daily.companies['600519.SH'].market;
  assert.equal(dm.return_1y, +(((1 + 0.2) / 2 - 1) * 100).toFixed(2)); // 指标价是快照价的 2 倍
  assert.equal(dm.dividend_yield, 4);
  assert.equal(dm.high_52w, row.price);
  assert.equal(dm.low_52w, row.price);
  assert.equal(daily.meta.metrics_date, snapshot.meta.quote_date);
  const stale = (await run(withMetrics('2000-01-01'))).context.__DATA__;
  assert.equal(stale.meta.metrics_date, null);
  assert.equal(stale.companies['600519.SH'].market.return_1y, original.companies['600519.SH'].market.return_1y);
  // 档案增量包：迁移后与迁移前页面数据一致；已核实改动能覆盖基线；快照不对应时忽略
  const bundlePath = path.join(web, 'live/profiles/bundle.json');
  if (fs.existsSync(bundlePath)) {
    const bundle = JSON.parse(fs.readFileSync(bundlePath, 'utf8'));
    const withBundle = (b) => async (url) => ({ ok: true, json: async () =>
      url.endsWith('live/manifest.json') ? manifest : url.endsWith('profiles/bundle.json') ? b : snapshot });
    const migrated = (await run(withBundle(bundle))).context.__DATA__;
    // 包里有意修订的已核实档案除外，其余公司与不读包时逐字段一致
    const revised = new Set(Object.entries(bundle.profiles).filter(([, p]) => p.status === 'verified_snapshot').map(([k]) => k));
    // 自动挑选的同行会随包内转正公司的行业变化，比较时去掉涉及转正公司的同行名单
    const plain = (c, other) => {
      const touched = [...c.peers.peer_codes, ...other.peers.peer_codes].some((x) => revised.has(x));
      return JSON.stringify(touched ? { ...c, peers: { ...c.peers, peer_codes: null } } : c);
    };
    for (const k of Object.keys(current.companies)) {
      if (!revised.has(k)) assert.equal(plain(migrated.companies[k], current.companies[k]), plain(current.companies[k], migrated.companies[k]), k);
    }
    for (const k of revised) {
      assert.equal(migrated.companies[k].profile_status, 'verified_snapshot');
      assert.equal(migrated.companies[k].identity.business_summary, bundle.profiles[k].profile.identity.business_summary);
      assert.ok(migrated.companies[k].peers.peer_codes.length > 0, `${k} 同行名单为空`);
    }
    const edited = JSON.parse(JSON.stringify(original.companies['600519.SH']));
    edited.identity.business_summary = '人工修订后的简介';
    const patched = (await run(withBundle({ meta: { snapshot: manifest.snapshot },
      profiles: { ...bundle.profiles, '600519.SH': { status: 'verified_snapshot', profile: edited } } }))).context.__DATA__;
    assert.equal(patched.companies['600519.SH'].identity.business_summary, '人工修订后的简介');
    assert.equal(patched.companies['600519.SH'].market.price, row.price);
    const other = (await run(withBundle({ meta: { snapshot: 'snapshots/other.json' },
      profiles: { '600519.SH': { status: 'verified_snapshot', profile: edited } } }))).context.__DATA__;
    assert.notEqual(other.companies['600519.SH'].identity.business_summary, '人工修订后的简介');
    // 复核转正的新进公司：已核实档案没有静态行情，仍用当日快照价
    const autoEntry = bundle.profiles['600016.SH'];
    if (autoEntry) {
      const { _meta, ...promotedProfile } = autoEntry.profile;
      const promoted = (await run(withBundle({ meta: { snapshot: manifest.snapshot },
        profiles: { ...bundle.profiles, '600016.SH': { status: 'verified_snapshot', profile: promotedProfile } } }))).context.__DATA__;
      const pc = promoted.companies['600016.SH'];
      assert.equal(pc.profile_status, 'verified_snapshot');
      assert.equal(pc.market.price, snapshot.members.find((r) => r.code === '600016.SH').price);
    }
  }
  // 机构持股：有统一口径文件时覆盖所有公司（含已核实档案），文件里没有的公司记 null 而不沿用旧口径
  const inst = { meta: { report_date: '2026-06-30', definition: '口径' }, companies: { '600519.SH': { ratio: 4.06, excluded: [] } } };
  const withInst = (await run(async (url) => ({ ok: true, json: async () =>
    url.endsWith('live/manifest.json') ? manifest : url.endsWith('inst_hold.json') ? inst : snapshot }))).context.__DATA__;
  assert.equal(withInst.companies['600519.SH'].shareholders.inst_hold_ratio, 4.06);
  assert.equal(withInst.companies['600519.SH'].shareholders.inst_hold_date, '2026-06-30');
  assert.equal(withInst.companies['601398.SH'].shareholders.inst_hold_ratio, null);
  assert.equal(JSON.stringify(withInst.companies['600519.SH'].financials), JSON.stringify(original.companies['600519.SH'].financials));
  const offline = await run(badFetch, 'file:');
  assert.equal(offline.context.__LIVE_STATE__.mode, 'offline');
  assert.equal(offline.context.__DATA__, offline.original);
  console.log('live frontend data contract: OK');
})().catch((error) => { console.error(error); process.exitCode = 1; });
