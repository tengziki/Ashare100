---
name: enrich-company
description: 复核并补齐 Ashare100 动态百强中新进榜、临界区或档案有问题的公司（行业、简介、财务、股东、分红、logo、公告、投资者问答），复核通过后转为已核实档案。用户说“补档”“复核新进公司”“补 logo/公告/问答”“处理档案告警/warning”“数据状态需关注”时使用。
---

# 新进公司补档与复核

脚本已经自动做了能自动的部分（行业映射、东方财富 F10 抓取、缓存；新进公司首次出现时的 logo、公告、问答）。本 skill 负责需要判断的部分：核对数值、处理 warning、决定是否转为已核实档案。**不要手工编造或估算任何数值**；查不到就留 null 并说明。

## 1. 看现状

```bash
python scripts/validate_profiles.py
```

- `ERROR … [verified]`：已核实档案违反硬规则，工作流会因此不提交，必须先处理。
- `WARN … [auto]`：自动补档待复核的问题；`WARN … [verified]` 多为早期亏损次新股的真实极端值，确认来源后可保留。
- 再看 `ashare100/ashare100/web/live/status.json` 中 `profiles.problems`（快照阶段补档失败的公司，含 logo/公告/问答的附加问题）。

## 2. 补抓失败的公司

```bash
python scripts/enrich_profiles.py --backfill
```

它会为当前快照的待补档公司补行业与档案、写历史档、为临界区预补档。7 天内成功的缓存不会重抓；要强制重抓某家，先删除 `ashare100/ashare100/profiles/companies/<代码>.json` 再运行。

logo、公告、问答（新进公司首次出现时 backfill 已自动获取；已转正或要单独补的公司手动运行）：

```bash
python scripts/enrich_extras.py <代码> [<代码> ...]          # 只补空的部分
python scripts/enrich_extras.py <代码> --refresh             # 强制重抓
```

- **公告**：巨潮近 12 个月，存最新 20 条与 12 个月总数。
- **问答**：沪市上证 e 互动（已回复 + 待回复，最新 8 条）。e 互动只返回近期内容，条数少是正常的；结果为空时先在 `https://sns.sseinfo.com/company.do?uid=<uid>` 页面确认是否真的没有。深市互动易在本机网络不可达，脚本会记为附加问题，不要手工编造问答。
- **logo**：取官网图标与 Google 图标服务中边长 ≥32px 的最大一张，写入 `web/logos/` 并重新生成 `logos.js`。记为 missing 时页面显示首字图标；可以人工找一张清晰的方形图标（官网、官方公众号头像、交易所公司页），放到 `web/logos/<代码>.png`，在 `logos.json` 写明 `source` 与 `via: "manual"`，再运行一次 `python scripts/enrich_extras.py <代码>` 让 `logos.js` 重新生成。不要用第三方图库里来源不明的图。
- 附加资料失败记在档案的 `_extras`，**不阻塞转正**；但要在复核记录里写明缺了什么。

行业映射不到（`EM2016「…」无对应申万一级`）时：查申万官网行业分类，确认后在 `ashare100/ashare100/profiles/sw_industry_map.json` 的 `codes` 下加 `{"sw1": "…", "sw2": "…", "source": "manual", "updated": "…"}`。只能用申万 2021 版 31 个一级行业名（见 `scripts/enrich_profiles.py` 的 `SW1`）。

## 3. 逐家复核自动补档

对 `profiles/companies/<代码>.json` 中要复核的每家公司：

1. **财务**：从巨潮资讯（cninfo.com.cn）该公司最近一份年报核对 2025 年营业总收入、归母净利润、ROE（加权）各一个数；差异 >1% 时把两边数值和链接列给用户，由用户决定，不要自己取舍。
2. **分红**：核对最近一个财年每股派息（含中期）与实施状态；只有“实施分配”计入。
3. **股东**：核对股东户数与截止日期；`inst_hold_ratio` 留 null（口径无法对齐，见脚本注释），不要填北向或其他口径的数代替。
4. **简介**：`business_summary` 可改写为 80 字以内的客观描述，不加评价性词语；`profile` 保留原文。
5. **公告/问答/logo**：抽查 2–3 条公告标题与日期是否和巨潮公告页一致；问答抽查一条原文；logo 打开页面看是否是该公司（不是集团母公司或无关网站的图标）。
6. 记录每家复核了什么、对照的链接。

取年报：先向 `https://www.cninfo.com.cn/new/information/topSearch/query` POST `keyWord=<6位代码>` 取 `orgId`（不一定是 `gssh0<代码>`，如中金为 `qsgn0000421`、新华保险为 `GD040482`），再向 `https://www.cninfo.com.cn/new/hisAnnouncement/query` POST `stock=<6位代码>,<orgId>`、`category=category_ndbg_szsh;category_bndbg_szsh`、`seDate=<起>~<止>`，返回的 `adjunctUrl` 拼在 `https://static.cninfo.com.cn/` 后下载。优先用**年度报告摘要**（几百 KB）而非全文（数 MB）。PDF 存到 scratchpad，用 `pdftotext -layout -enc UTF-8` 转文本。

复核时的已知陷阱（2026-10-27 民生银行实测）：
- **摘要里的“主要会计数据”表格转文本后列会错位一行**，数值会对到上一行的名称上。以正文叙述句里的数为准（如“实现营业收入 1,428.65 亿元”“归属于本行股东的净利润 195.37 亿元”），或按行序逐个核对。
- **前十大股东“持股变动”口径不同**：年报/半年报写的是“报告期内增减”（较上年末），东财与已核实档案用的是“较上期”（较上一季度），两者不一致属正常，不要改。东财的“不变”记 0、“新进”记 null。
- 银行等未进前十大的北向持股、机构持股无法由此来源得到，保持 null。
- **本机 Python 连 `sns.sseinfo.com` 的 IPv4 会被断开**（SSL EOF），IPv6 正常；`enrich_extras.py` 失败时会自动改走 IPv6。若仍失败，用 `curl` 确认站点可达后再判断。
- 小官网图标（16×16）会被拒用，Google 图标服务对未知站点返回 16×16 默认地球图标，同样拒用。沪市公司优先用上证 e 互动的公司头像（`resources/images/avatar/company/<代码>.png`，公司自己上传，78px），拿到就不再访问官网（不少官网很慢或打不开）。
- **股本有重大变动（吸收合并、增发、回购注销）时**，东财的前十大股东可能比最近的定期报告新（2026-10 中金公司换股吸收合并东兴、信达后，汇金持股由 40.11% 变为 24.41%）。此时用巨潮上的“股份变动/新增股份上市公告”核对，并在 `review.note` 写明前十大的截止日；股东户数仍是定期报告日期，页面会分开标注两个日期。

## 4. 复核通过后转正

```bash
python scripts/profiles_store.py --promote <代码> --by <审核人> --note "核对 2025 年报营收/净利/ROE 一致"
python scripts/validate_profiles.py <代码>
python scripts/build_profile_bundle.py
```

转正后档案写入 `profiles/verified/`，带 `review` 字段（审核人、时间、来源）；页面上的“自动补档 · 待复核”徽章会消失。已核实档案不会被脚本覆盖，之后的修订直接改该文件（例如把 `business_summary` 由经营范围原文改写成简短描述，并在 `review.note` 里注明）。同行名单为空时，页面自动取同一申万一级、市值最接近的 5 家。

`--by` 写实际复核人；由 Claude 代为复核时写明“Claude（代 <用户> 复核）”，不要冒用用户本人名义。

公告与问答目前只在首次出现时获取一次；纳入每日两次更新是另一项工作，不在本 skill 内。

## 5. 收尾

```bash
python -m unittest discover -s scripts/tests -p "test_*.py"
node scripts/tests/test_live_frontend.js
python scripts/validate_pages_bundle.py
```

全部通过后，**询问用户**是否提交；提交只包含 `ashare100/ashare100/profiles/`、`ashare100/ashare100/web/live/profiles/` 以及新增的 logo（`web/logos/` 与 `web/logos.js`）。提交说明列出：转正了哪些公司、每家核对的字段与来源链接、仍留空的字段及原因。
