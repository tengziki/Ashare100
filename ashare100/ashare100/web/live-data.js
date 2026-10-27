/* 在线榜单入口：先校验 manifest 和快照，再让 app.js 建立页面索引。 */
(() => {
  const STATIC = window.__DATA__;
  const BASE = 'live/';
  const CODE = /^\d{6}\.(SH|SZ|BJ)$/;
  const FIELDS = ['price', 'change_pct', 'amount', 'turnover_rate', 'pe_ttm', 'pb', 'total_mv', 'float_mv'];
  const finite = (value) => typeof value === 'number' && Number.isFinite(value);
  const fetchJson = async (path) => {
    const response = await fetch(path, { cache: 'no-store' });
    if (!response.ok) throw new Error(`${path}: HTTP ${response.status}`);
    return response.json();
  };
  const validPath = (path) => typeof path === 'string' && /^snapshots\/[\w-]+\.json$/.test(path);

  function validate(manifest, snapshot) {
    if (!validPath(manifest?.snapshot) || !snapshot?.meta || !Array.isArray(snapshot.members)) throw new Error('动态榜结构异常');
    const meta = snapshot.meta;
    if (meta.ranking_metric !== 'total_mv_CNY' || !/^\d{4}-\d{2}-\d{2}$/.test(meta.quote_date) ||
        meta.quote_date !== manifest.quote_date || meta.member_count !== 100 ||
        meta.quoted_count !== meta.listed_count || meta.listed_count < 5000 || snapshot.members.length !== 100 ||
        !['intraday_provisional', 'close_confirmed'].includes(meta.ranking_status)) throw new Error('动态榜元数据不完整');
    const codes = new Set();
    snapshot.members.forEach((row, index) => {
      if (!CODE.test(row.code) || codes.has(row.code) || row.rank !== index + 1 ||
          !finite(row.total_mv) || row.total_mv <= 0 || !finite(row.price) || row.price <= 0 ||
          !/^\d{14}$/.test(row.quote_at) || row.quote_at.slice(0, 8) !== meta.quote_date.replace(/-/g, '') ||
          (index && row.total_mv > snapshot.members[index - 1].total_mv)) throw new Error('动态榜成员不完整或排序错误');
      codes.add(row.code);
    });
    return true;
  }

  function pendingCompany(row, profile, quoteDate) {
    const [code, exchange] = row.code.split('.');
    return {
      profile_status: 'pending',
      identity: {
        code, exchange, name_abbr: row.name, name_full: row.name,
        board: profile?.board || '待核实板块', industry_sw1: profile?.industry_sw1 || '行业待补',
        industry_sw2: '待补', aliases: [], former_names: [], list_date: null,
      },
      market: { as_of_date: quoteDate, quote_at: row.quote_at, index_members: [],
        price: row.price, change_pct: row.change_pct, amount: row.amount,
        turnover_rate: row.turnover_rate, pe_ttm: row.pe_ttm, pb: row.pb,
        total_mv: row.total_mv, float_mv: row.float_mv,
        ytd_return: null, return_1y: null, return_3y: null, dividend_yield: null },
      financials: { annual: Array.from({ length: 5 }, (_, i) => ({ fiscal_year: 2021 + i })), latest: {}, derived: {} },
      shareholders: { holder_num_date: null, inst_hold_ratio: null, northbound_ratio: null },
      dividends: { annual: Array.from({ length: 5 }, (_, i) => ({ fiscal_year: 2021 + i })), consecutive_years: null },
      peers: { peer_codes: [], segments: [] },
      announcements: { items: [], count_12m: null }, qa: { items: [] },
    };
  }

  function combine(snapshot) {
    const companies = {};
    for (const row of snapshot.members) {
      const old = STATIC.companies[row.code];
      companies[row.code] = old ? {
        ...old,
        profile_status: 'verified_snapshot',
        market: {
          ...old.market,
          as_of_date: snapshot.meta.quote_date,
          quote_at: row.quote_at,
          ...Object.fromEntries(FIELDS.map((field) => [field, row[field] ?? null])),
          pe_static: null, ps_ttm: null, high_52w: null, low_52w: null,
          ytd_return: null, return_1y: null, return_3y: null, dividend_yield: null,
        },
      } : pendingCompany(row, snapshot.pending_profiles?.[row.code], snapshot.meta.quote_date);
    }
    return {
      ...STATIC,
      meta: { ...STATIC.meta, as_of_date: snapshot.meta.quote_date, live: snapshot.meta,
        live_changes: snapshot.changes, static_profile_date: STATIC.meta.as_of_date },
      companies,
    };
  }

  async function load() {
    if (location.protocol === 'file:') {
      window.__LIVE_STATE__ = { mode: 'offline', reason: '本地文件使用静态快照' };
      return;
    }
    try {
      const manifest = await fetchJson(BASE + 'manifest.json');
      if (!validPath(manifest?.snapshot)) throw new Error('manifest 快照路径异常');
      const snapshot = await fetchJson(BASE + manifest.snapshot);
      validate(manifest, snapshot);
      window.__DATA__ = combine(snapshot);
      window.__LIVE_STATE__ = { mode: 'live', snapshot: manifest.snapshot };
    } catch (error) {
      console.warn('动态榜加载失败，显示静态备份：', error);
      window.__LIVE_STATE__ = { mode: 'fallback', reason: error.message };
    }
  }

  window.__LIVE_READY__ = load();
  window.__LIVE_CHECK__ = async () => {
    if (window.__LIVE_STATE__?.mode === 'offline' || document.hidden) return;
    try {
      const manifest = await fetchJson(BASE + 'manifest.json');
      if (!validPath(manifest?.snapshot) || manifest.snapshot === window.__LIVE_STATE__?.snapshot) return;
      validate(manifest, await fetchJson(BASE + manifest.snapshot));
      location.reload(); // 保留当前 hash，重新建立所有派生索引
    } catch (error) { console.warn('新榜单尚未就绪：', error); }
  };
})();
