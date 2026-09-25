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
| 财务三表（营收/利润/现金流/ROE） | baostock 季频明细接口（query_profit_data 等） | 待现场探查（接口存在，**本项目未接线**，公布日字段可得性未验证） | 未接 | ⚠️ F4/F5 因子接线前必须先跑 external 探查脚本；无公布日则只能 live，不进回测 |
| 分部营收/业务暴露 | 无稳定自动接口 | 不可得 | 无 | 走 `user_asserted_record` 人工通道（DESIGN 允许，USER_ASSERTED 标记输入者/时间/适用期） |
| ST 状态/股东户数/融资余额 | baostock/akshare | **当前值 only，非 PIT**（ISS-053 实证） | 已接（live 安全网/top_signal） | live only；回测路径保持禁用（既有纪律不变） |
| RAG 检索块 | 本地 FAISS | 方法文本，非事实 | 已接 | qualify 默认排除——不当公司事实 |

**首发行业范围结论**（TASKS F3"发现不足则缩小首发范围并明示"）：严格 PIT 回测可用的
证据类别=**行情 + 业绩预告 + 官方公告**三类；财务三表待公布日字段现场验证后才可进
回测。长期（LONG）质量研究所需的 12 季度财务包（DESIGN §5.2）在财务三表接线验证前
**不启动**——不填通用数字强行纳入。

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
