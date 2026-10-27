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
        industry_sw2: profile?.industry_sw2 || '二级待补', industry_source: profile?.industry_source || null,
        aliases: [], former_names: [], list_date: null,
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

  // 自动补档公司：身份、财务、股东、分红来自补档脚本，行情来自当前快照
  function autoCompany(row, profile, quoteDate) {
    const { _meta, ...rest } = profile;
    const dps = [...(profile.dividends?.annual || [])].reverse().find((a) => finite(a.dps_pretax))?.dps_pretax;
    return {
      ...rest, profile_status: 'auto_profile', profile_meta: _meta,
      market: { as_of_date: quoteDate, quote_at: row.quote_at, index_members: [],
        ...Object.fromEntries(FIELDS.map((field) => [field, row[field] ?? null])),
        pe_static: null, ps_ttm: null, high_52w: null, low_52w: null,
        ytd_return: null, return_1y: null, return_3y: null,
        dividend_yield: finite(dps) && row.price > 0 ? +(dps / row.price * 100).toFixed(2) : null },
    };
  }

  // 静态档案的价格类指标按今日价换算：年初至今与股息率的基准（年末价、近 12 月派息）不随日变，
  // 可直接重算；52 周高低并入今日价；1 年/3 年、静态 PE、PS 沿用静态值并标注日期。
  function derived(old, price) {
    const ok = finite(old.price) && old.price > 0 && finite(price) && price > 0;
    const ratio = ok ? old.price / price : null;
    return {
      ytd_return: ok && finite(old.ytd_return) ? +(((1 + old.ytd_return / 100) / ratio - 1) * 100).toFixed(2) : null,
      dividend_yield: ok && finite(old.dividend_yield) ? +(old.dividend_yield * ratio).toFixed(2) : null,
      high_52w: finite(old.high_52w) ? Math.max(old.high_52w, price) : null,
      low_52w: finite(old.low_52w) ? Math.min(old.low_52w, price) : null,
      stale_fields: { date: old.as_of_date, keys: ['return_1y', 'return_3y', 'pe_static', 'ps_ttm'] },
    };
  }

  // 进出榜：优先用快照自带的「较上一交易日收盘」；旧快照没有时，以静态档案的收盘百强为基准现场计算
  function changesVsClose(snapshot) {
    if (snapshot.changes?.vs_prev_close) return snapshot.changes.vs_prev_close;
    const staticRank = Object.values(STATIC.companies)
      .sort((a, b) => b.market.total_mv - a.market.total_mv)
      .map((c, i) => [`${c.identity.code}.${c.identity.exchange}`, { name: c.identity.name_abbr, rank: i + 1 }]);
    const old = new Map(staticRank);
    const now = new Map(snapshot.members.map((r) => [r.code, r]));
    return {
      base_snapshot: null, base_label: `${STATIC.meta.as_of_date} 收盘（静态基准）`,
      entered: snapshot.members.filter((r) => !old.has(r.code)).map((r) => ({ code: r.code, name: r.name, rank: r.rank, prev_rank: null })),
      exited: staticRank.filter(([code]) => !now.has(code)).map(([code, o]) => ({ code, name: o.name, prev_rank: o.rank, now_rank: null, total_mv: null })),
      rank_change: Object.fromEntries(snapshot.members.filter((r) => old.has(r.code) && old.get(r.code).rank !== r.rank)
        .map((r) => [r.code, old.get(r.code).rank - r.rank])),
    };
  }

  // 日更指标（build_daily_metrics.py）：同一交易日才采用；价格若与快照不同，按快照价换算
  function applyMetrics(c, m, price) {
    if (!m || !finite(m.price) || m.price <= 0 || !finite(price)) return;
    const k = m.price / price;
    const ret = (v) => (finite(v) ? +(((1 + v / 100) / k - 1) * 100).toFixed(2) : null);
    Object.assign(c.market, {
      ytd_return: ret(m.ytd_return), return_1y: ret(m.return_1y), return_3y: ret(m.return_3y),
      high_52w: finite(m.high_52w) ? Math.max(m.high_52w, price) : null,
      low_52w: finite(m.low_52w) ? Math.min(m.low_52w, price) : null,
      dividend_yield: finite(m.dividend_yield) ? +(m.dividend_yield * k).toFixed(2) : null,
      metrics_date: m.date, stale_fields: { date: STATIC.meta.as_of_date, keys: ['pe_static', 'ps_ttm'] },
    });
  }

  function combine(snapshot, metricsDoc = null, bundle = null, disclosures = null) {
    const companies = {};
    // 档案增量包：与当前快照对应时，已核实改动覆盖 data.js 基线，自动补档优先于快照内嵌档案
    const extra = bundle?.meta?.snapshot === snapshot.meta.snapshot_path ? bundle.profiles || {} : {};
    const metrics = metricsDoc?.meta?.quote_date === snapshot.meta.quote_date ? metricsDoc.metrics || {} : {};
    for (const row of snapshot.members) {
      const add = extra[row.code];
      const old = add?.status === 'verified_snapshot' ? add.profile : STATIC.companies[row.code];
      const autoProfile = add?.status === 'auto_profile' ? add.profile : snapshot.pending_profiles?.[row.code]?.profile;
      companies[row.code] = old ? {
        ...old,
        profile_status: 'verified_snapshot',
        market: {
          ...old.market,
          as_of_date: snapshot.meta.quote_date,
          quote_at: row.quote_at,
          ...Object.fromEntries(FIELDS.map((field) => [field, row[field] ?? null])),
          ...derived(old.market || {}, row.price), // 复核转正的档案没有静态行情
        },
      } : autoProfile ? autoCompany(row, autoProfile, snapshot.meta.quote_date)
        : pendingCompany(row, snapshot.pending_profiles?.[row.code], snapshot.meta.quote_date);
    }
    // 每日两更的公告与问答（update_disclosures.py）：有则覆盖档案中的同名字段
    for (const [code, d] of Object.entries(disclosures?.companies || {})) {
      const c = companies[code];
      if (!c) continue;
      if (d.announcements?.items) c.announcements = d.announcements;
      if (d.qa?.items) c.qa = d.qa;
    }
    for (const row of snapshot.members) {
      if (metrics[row.code]) applyMetrics(companies[row.code], { ...metrics[row.code], date: snapshot.meta.quote_date }, row.price);
    }
    // 没有同行名单的公司（自动补档、复核转正）：当前百强中同一申万一级、市值最接近的 5 家
    for (const [code, c] of Object.entries(companies)) {
      if (c.peers?.peer_codes?.length) continue;
      const mv = c.market.total_mv;
      c.peers = { ...c.peers, peer_codes: Object.entries(companies)
        .filter(([k, o]) => k !== code && o.identity.industry_sw1 === c.identity.industry_sw1)
        .sort(([, a], [, b]) => Math.abs(a.market.total_mv - mv) - Math.abs(b.market.total_mv - mv))
        .slice(0, 5).map(([k]) => k) };
    }
    return {
      ...STATIC,
      meta: { ...STATIC.meta, as_of_date: snapshot.meta.quote_date, live: snapshot.meta,
        live_changes: changesVsClose(snapshot), live_changes_run: snapshot.changes?.vs_prev_run || null,
        live_watchlist: snapshot.watchlist || [],
        metrics_date: Object.keys(metrics).length ? snapshot.meta.quote_date : null,
        static_profile_date: STATIC.meta.as_of_date },
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
      // 日更指标可缺省：读不到时退回按静态档案换算
      const metricsDoc = await fetchJson(BASE + 'metrics/latest.json')
        .then((p) => (/^metrics\/\d{8}\.json$/.test(p?.file) ? fetchJson(BASE + p.file) : null))
        .catch(() => null);
      const bundle = await fetchJson(BASE + 'profiles/bundle.json').catch(() => null);
      const disclosures = await fetchJson(BASE + 'disclosures/latest.json').catch(() => null);
      window.__DATA__ = combine({ ...snapshot, meta: { ...snapshot.meta, snapshot_path: manifest.snapshot } }, metricsDoc, bundle, disclosures);
      // 数据任务状态（status.json）可缺省：只用于页脚面板
      window.__DATA__.meta.task_status = await fetchJson(BASE + 'status.json').catch(() => null);
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
