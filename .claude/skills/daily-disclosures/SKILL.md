---
name: daily-disclosures
description: Ashare100 百强公司公告与投资者问答的每日两次更新（北京时间 12:00 / 15:00 随榜单工作流运行）：手动补跑、排查“公告与投资者问答”数据状态需关注/已过期、处理单家抓取失败。用户说“更新公告”“更新问答”“公告问答没更新”“数据状态里公告需关注”时使用。新进公司首次补档请用 enrich-company。
---

# 公告与问答每日两更

公告与问答是会变的数据：存在 `ashare100/ashare100/web/live/disclosures/latest.json`，页面加载时覆盖档案里的同名字段；**不写进** `profiles/verified/`。由 `.github/workflows/update_live_quotes.yml` 在每次榜单更新后运行 `scripts/update_disclosures.py`（`continue-on-error`，失败不拦榜单）。

| 数据 | 来源 | 规则 |
|---|---|---|
| 公告 | 巨潮资讯公告查询（近 12 个月） | 每次整体重取最新 20 条与 12 个月总数 |
| 问答 | 沪市：上证 e 互动（已回复 + 待回复） | 与上一版按（提问日, 问题）合并：新问题加入，旧问题出现回复时更新，取最新 8 条；新一版没带回复时不抹掉旧回复 |
| 问答 | 深市 / 北交所：巨潮互动易 | 本机网络不可达，未实现；沿用档案原有问答。这是已知缺口，只在状态说明里计数（“深市问答暂不支持 N 家”），不算问题、不触发“需关注”。**不要手工编造问答** |

单家失败沿用上一版该公司的数据；当日成功更新公告的公司少于 80% 时不切换 `latest.json`（退出码 1）。巨潮 orgId 与 e 互动 uid 缓存在 `ashare100/ashare100/profiles/disclosure_ids.json`。

## 1. 看状态

```bash
python -X utf8 -c "import json;t=json.load(open('ashare100/ashare100/web/live/status.json',encoding='utf-8'))['tasks'].get('disclosures');print(t and {k:t[k] for k in ('last_success','detail','problems')})"
```

页面页脚“数据状态”里同名一行：超过约 1 个交易日未成功显示“需关注”，2 个交易日“已过期”；有问题清单也显示“需关注”。

## 2. 手动补跑

```bash
python scripts/update_disclosures.py                      # 全部百强（2026-10-27 本机实测约 4 分钟）
python scripts/update_disclosures.py --only 601995.SH     # 只更新指定公司，其余沿用上一版；不改写 status.json
python scripts/validate_pages_bundle.py
```

## 3. 排查

- **e 互动失败（SSL EOF / URLError）**：本机 Python 连 `sns.sseinfo.com` 的 IPv4 会被断开，脚本自动改走 IPv6，因此每家要先等 IPv4 超时，全量较慢。先用 `curl -s "https://sns.sseinfo.com/ajax/getCompany.do?data=600016"` 确认站点本身是否可达；站点不可达时（2026-10-27 傍晚出现过一次，可能是请求过多被限流）隔一段时间再跑，不要加大并发。
- **问答为空**：e 互动只返回近期内容。到 `https://sns.sseinfo.com/company.do?uid=<uid>` 页面确认是否真的没有，再下结论。
- **公告为 0 或报“巨潮检索不到”**：orgId 不一定是 `gssh0<代码>`。删掉 `disclosure_ids.json` 里该公司的缓存再跑一次，脚本会重新查。
- **覆盖不足 80%**：多半是巨潮整体不可达；保留上一版即可，不要用 `--only` 拼出一份“看起来完整”的文件冒充当日更新。

## 4. 收尾

```bash
python -m unittest discover -s scripts/tests -p "test_*.py"
node scripts/tests/test_live_frontend.js
python scripts/validate_pages_bundle.py
```

全部通过后**询问用户**是否提交；手动补跑的提交只包含 `ashare100/ashare100/web/live/disclosures/`、`web/live/status.json` 与 `profiles/disclosure_ids.json`。
