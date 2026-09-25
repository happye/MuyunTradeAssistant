# 数据契约与可得性覆盖报告（plan/fusion F3 交付）

2026-09-25。对应 DESIGN §5.1（EvidenceRecord/EvidenceSnapshot）与 TASKS.md F3。
**诚实声明：本报告基于源码核对与既有纪律记录（ISS-052/053），未做真实接口现场探查**；
标「待现场探查」的行由 `tests/data_sources/test_all_api.py`（external，opt-in）扩展验证，
不以此报告代替真实覆盖。

## 1. 证据契约（src/data/research_snapshot.py，16 测试锁死）

| 契约 | 语义 |
|---|---|
| `EvidenceRecord` | published_at（来源公布）/ available_at（系统可用，**严格PIT唯一判据**）/ fetched_at（本次抓取）三分立；`period_end ≠ available_at`；unit/currency/period_kind（累计/单季）显式；revision_id 支持后发重述；extra=allow 未知字段保留 |
| `EvidenceSnapshot.build(strict=True)` | PIT 闸门：available_at 缺失或 > as_of 一律拒收（未来公告/后发重述不进旧快照），被拒数量与 id 如实记录 |
| `snapshot_id` | 内容 hash（evidence_id/fetched_at/入序无关）——同快照重放稳定，同股重放可比 |
| `qualify(required)` | 按必需清单判 COMPLETE/INCOMPLETE/CONFLICTED；缺失/过期/冲突显式命名；**默认排除 source_kind=rag**（方法文本不当公司事实） |
| `normalize_amount` | 元/万元/亿元 显式换算；未登记单位拒猜（G15 红线） |
| `financial_record` | period_kind 必填（累计/单季）——缺口径拒绝构造 |

## 2. 可得性矩阵（按证据类别）

| 类别 | 数据源（现有） | PIT 能力 | 现状 | 首发决策 |
|---|---|---|---|---|
| 日线行情 | baostock query_history_k_data / akshare 备源 | **天然 PIT**（historical bar 自带日期） | 已接线（data_feeder/akshare_client） | ✅ F3 直接进证据层（market_bar_record） |
| 业绩预告 | baostock query_forecast_report | **可 PIT**（profitForcastExpPubDate 官方公布日） | 已接线（AKShareClient.get_latest_forecast，ISS-053 用） | ✅ 进证据层（forecast_record） |
| 官方公告 | 巨潮 disclosure_report（akshare，经 get_recent_announcements 备源） | **可 PIT**（公告日期官方） | 备源已接；巨潮 DataFrame 无来源列，**F3 审查 P1-3 修复：data_provider 备源分支已打 source="巨潮公告" 标**（research_snapshot official_date 判定依赖） | ✅ 进证据层（official_date=True）；注意主源有数据时不走巨潮分支 |
| 个股新闻 | 东财 stock_news_em | **不可回填历史快照**（每条自带发布时间，但"最近N天"窗口接口无法对历史 as_of 重建当时视图） | 主源已接（live 用） | ⚠️ live 视图可见；**严格快照必拒**（与 ISS-052"回测不填公告"纪律同构，机器化到证据层） |
| 财务三表（营收/利润/现金流/ROE） | baostock 季频明细（query_profit_data / query_balance_data / query_cash_flow_data / query_operation_data / query_growth_data） | **✅ 已现场探查可 PIT**（2026-09-26，`tests/data_sources/probe_fin_pubdate.py`，产物 `tests/artifacts/probe_fin_pubdate.log`）：五接口全有 `pubDate` 官方公布日（样本：600519 2023Q4 → pubDate 2024-04-03），同期重复查询一致 | **已接**（`src/data/financial_data.py` 窄适配 fetcher：五接口→fin dict，单位实测锚定「货币=元/比例=小数比值」，缺失空串→None；**北交所代码 baostock 不支持季频财务**——降级告警留痕、永久零财务证据，登记为范围限制；生产分析路径消费随 E2/E4） | ✅ **财务证据可进严格 PIT 快照**（E2/E4 财务因子解锁）；**重述限制**：接口只返回最新一版，后发重述与首发不可区分（VALIDATION §2 部分满足）；长期（LONG）12 季度财务包探查前置已满足——**全量覆盖验证**（分层抽样只证可得性）在 E2/E4 接线批做 |
| 分部营收/业务暴露 | 无稳定自动接口 | 不可得 | 无 | 走 `user_asserted_record` 人工通道（DESIGN 允许，USER_ASSERTED 标记输入者/时间/适用期） |
| ST 状态/股东户数/融资余额 | baostock/akshare | **当前值 only，非 PIT**（ISS-053 实证） | 已接（live 安全网/top_signal） | live only；回测路径保持禁用（既有纪律不变）。注：baostock K线 `isST` 字段日频可得（探查实证可用），历史 ST 状态可经 K线回溯——与实时接口的"当前值 only"是两回事 |
| 行业数据（需求量/价格/库存） | industry_data.py（chat 行业分析文本报告层） | **无结构化序列、无 PIT**（TTL 缓存 1 小时 + 缺失/过期降级标注，live-only） | 文本层已接（chat 用） | ⚠️ 不能作 PIT 证据；结构化需求量数据待现场探查接线（demand_change_v1 依赖） |
| 行业指数 | akshare `index_hist_sw`（申万指数日线）+ baostock query_stock_industry（行业归属） | **✅ 已现场探查**（同上产物）：`index_hist_sw('801010','day')` 6463 行 OHLC+量额（1999 起），指数 bar 天然 PIT；行业归属为**当前值**（updateDate 语义=最近更新，**历史行业归属变化不可回溯**——历史重放用行业归属需谨慎） | 未接线 | ✅ relative_trend_v1 的分母可接（接线随 F5 因子计算批）；行业归属作静态映射时须登记 updateDate caveat |
| 停牌状态 | baostock query_history_k_data_plus `tradestatus` 字段（1 正常/0 停牌） | **✅ 已现场探查**（同上产物）：字段可用，日频随 K线返回 | 未接线（data_feeder 现不含该字段） | ✅ trading_capacity_v1 状态分量可接（接线随 DataFeeder 字段扩展；ratio 主计算不受阻） |
| RAG 检索块 | 本地 FAISS | 方法文本，非事实 | 已接 | qualify 默认排除——不当公司事实 |

**首发行业范围结论**（TASKS F3"发现不足则缩小首发范围并明示"；2026-09-26 探查后更新）：严格 PIT 回测可用的
证据类别=**行情 + 业绩预告 + 官方公告 + 财务季频五接口（pubDate 已探查实证）**；行业指数/停牌状态接口也已
实证可得（接线后可用）。**接线与全量覆盖验证**（抽样只证可得性，不代全量）在 E2/E4 财务因子接线批进行；
长期（LONG）质量研究所需的 12 季度财务包（DESIGN §5.2）探查前置已满足，接线批启动。

## 3. 验收对照（TASKS F3）

| 验收条款 | 落点 | 测试 |
|---|---|---|
| 未来公告和后发重述不进入旧快照 | strict PIT 闸门 + revision_id | `test_future_announcement_excluded_from_old_snapshot` / `test_late_restatement_does_not_enter_old_snapshot` / `test_market_bar_pit_natural` |
| 元/万元及累计/单季口径可验证 | normalize_amount + period_kind 必填 | `test_unit_conversion_explicit` / `test_period_kind_must_be_explicit_for_financial` / `test_qualify_respects_period_kind` |
| 删必需字段后资格变 INCOMPLETE | qualify 显式命名缺失 | `test_missing_required_evidence_incomplete_named` / `test_stale_evidence_flagged` / `test_missing_quality_status_excluded` |
| RAG 方法文本不当公司事实 | qualify 默认排除 rag | `test_rag_text_not_company_fact` |
| 同快照重放稳定 | snapshot_id 内容 hash | `test_snapshot_id_stable_across_replay_and_order` |
| 回测路径零实时网络 | 模块层禁网络 import（源码守卫） | `test_module_import_is_network_free` |
| 证据异常/过期/未知分开 | quality_status 七态 + 时效判定 | `test_conflicting_evidence_reported` / `test_stale_evidence_flagged` |

## 4. 回滚与告警纪律

- 回滚：适配器旁路（现有 provider 调用方零改动；证据层纯增量）；缺字段不补假数据
- 本批零新增 logger.warning（纯函数层，异常走 ValueError + 测试锁）；后续 live 接线
  若新增告警，按"人话化三处同步"纪律同 commit 落 plain_errors + 速查手册
- **可得时点约定（F3 审查 P2-2）**：披露类证据的 available_at 统一取披露日 23:59+08:00
  （保守——披露常在盘后）；回放 as_of 若取盘中会自然排除当日披露，杜绝日内前视窗
