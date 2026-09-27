# R1/R2 数据与证据可信度设计

2026-09-26｜2026-09-27 更新：**R1/R2 已实施**（§1–§5 大部落码，见 [EXECUTION_RECORD.md](EXECUTION_RECORD.md) R1/R2 节与五维判定）｜配套 [验收裁决](README.md)、[任务卡](TASKS.md)。本设计细化原 ADR-F05/F06，不改变"缺失不补假数据、方法文本不当公司事实"的原则。

## 1. 不再用一个 PIT 布尔值包办资格

现有 `available_at <= as_of` 是必要检查，无法证明输入数值的版本确实在当时存在。`financial_record` 今天抓取最新版值、配一个过去的 pubDate，就能通过 strict；增加 fetched_at<=as_of 的简单判断也不对，因为今天下载一份有可信发布日期的原始历史公告可以合法回放。

在 `EvidenceRecord` 版本化扩展以下语义，旧记录缺字段必须迁移为 UNKNOWN，不能自动补绿：

| 字段 | 含义 |
|---|---|
| `knowledge_basis` | `AS_PUBLISHED_ARCHIVE` / `CONTEMPORANEOUS_CAPTURE` / `LATEST_WITH_PUBLICATION_DATE` / `UNKNOWN` |
| `document_version_id` | 原始文档/响应内容 hash 加来源的版本标识；adapter 的代码版本另存 |
| `first_seen_at` | 本系统第一次见到**该版本内容**的时点，追加存储不可回拨 |
| `version_available_at` | 有来源证明的该版本公开时点；不是无条件复制报告首次发布日期 |
| `timestamp_precision` | datetime/day/unknown；仅日级采用保守下个可用决策时点 |
| `provenance_evidence_ids` | 支持版本和时间认定的原始文档/档案引用 |
| `semantic_status` | VERIFIED / SUSPECT / UNKNOWN；指单位与指标定义通过检查，非投资观点正确 |
| `raw_value` / `raw_unit` | 源数据原貌，与 canonical value/unit 并存 |
| `metric_definition_version` | 字段的经济含义、量纲、时间基准、合并范围、派生公式版本 |

`FactStatus` 仍区分 OBSERVED / USER_ASSERTED / MODEL_INFERRED 等。**事实来源类别、内容核验、历史版本可得性是三个不同轴**，不要继续堆到一个 quality_status 字符串里。

严格历史资格算法：

1. 检查实体、内容版本、指标语义及所需时间精度；冲突/可疑先隔离。
2. AS_PUBLISHED_ARCHIVE：引用到原始版本及其公开证明，取 version_available_at；后续修订必须独立版本。
3. CONTEMPORANEOUS_CAPTURE：保守用该版本 first_seen_at；只支持其后决策，不能把今天积累的数据回填到去年。
4. LATEST_WITH_PUBLICATION_DATE：只支持当前研究或明确标记的非严格敏感性分析，不进 strict 历史组。
5. UNKNOWN：不进 strict；诊断保留缺口。
6. 通过上述资格后再判断有效可用时点<=as_of。截止之后的修改只能影响新快照，禁止改旧快照原件。

USER_ASSERTED 可以支持当前人工辅助计划，但不能仅凭用户填了过去日期进入严格历史效果样本。用户上传的原始历史文件可按同一档案核验规则升级来源能力；AI 转写文件也不会自动改变文件的版本资格。

工程 API 建议：`assess_evidence_eligibility(record, context) -> Eligibility(status, reasons, effective_available_at)`，供 `EvidenceSnapshot.build` 与研究资格共用。strict 拒收数按原因分类，不只计 future/missing 两种。live 不等于绕过未来检查、单位校验、实体检查；只是可使用“当前可得但无法证明历史版本”的证据。

## 2. ISS-114：识别可疑，不自动乘以 100

交付报告称万科 liabilityToAsset 出现约百倍量级变化。本轮没有查询原始财报验证正确数值；**问题身份先登记为源字段跨期口径可疑，不能把 .7322 或 .0073 任一值直接硬编码为真值**。

R1 的窄处理链：

1. 原始响应完整留样，至少保留字段名、接口参数、查询时点、源版本、报告期。
2. 同公司同语义、同报告范围的相邻期量级跳变触发 SUSPECT。超过 10 倍可作为本事故的异常筛查初值，非正确性证明；零值、符号变化、重组、合并范围变化须单独分支。
3. 与发行人原始财报同报告期、同合并口径的分子分母计算结果交叉核对；额外数据源只能交叉佐证，不能用多数投票决定真值。
4. 若确认供应商字段/期间发生单位变化，增加**有适用范围和证据的版本化映射**；不对所有股票所有年份全局乘 100。
5. 未核实的字段不能用于长期资格、风险预算、排名优选。其他可信字段仍能展示，受影响计划变 REVIEW_REQUIRED 并列出待核对事项。
6. 反向遍历 `evidence -> factor -> assessment -> plan/proposal/experiment`，登记影响清单。撤销未执行提案的资格、重算当前派生结果；已确认成交保持事实，不回写成未成交。

已有 E1/E7 使用财务量级可疑样本，只能标“待重验”，不能未核实就说其候选全错。旧实验产物不删除、不原地改数值；追加失效原因并产生新 run_id。

## 3. 财务时间口径不能全设 cumulative

`financial_data._INTERFACE_FIELDS` 混有金额、比率、股数、增长率与 epsTTM，现统一赋 cumulative。R1 要为每个字段登记 `value_kind`、`period_basis`、`unit` 与派生规则：

| 类别 | 示例含义 | 允许的派生 | 禁止的操作 |
|---|---|---|---|
| FLOW | 一个期间的收入/现金流/利润金额 | 同年同口径累计差分得到单季；单季拼接 TTM | 跨年直接累计相减；不同修订/合并范围相减 |
| STOCK | 期末股数、资产负债科目 | 用明确期末值/平均余额按定义计算 | 把期末股数当累计量差分 |
| RATIO | 资产负债率、利润率、ROE、CFO/净利 | 从一致口径分子分母重建；保留来源比率定义 | 两个累计比率相减得到单季比率 |
| GROWTH | 同比增长率 | 核对比较基期与分子分母；低基数标记 | 作为累计流量；亏损扭亏时直接跨公司线性排名 |
| PER_SHARE / TTM | TTM 每股指标 | 校验股本和调整基准，按定义计算 PE | 把 TTM EPS 当 YTD 利润差分 |

period_basis 至少能表达 POINT_IN_TIME / YTD / SINGLE_QUARTER / TTM / COMPARATIVE / UNKNOWN。比率还需 underlying_period_basis。具体 Baostock 各字段属于哪类，必须逐项用可获得的供应商定义及原始报告校对后注册；本设计不依据一个茅台样本推定全部接口口径。

本轮访问 [Baostock 官方 API 页面](https://baostock.com/baostock/index.php/Python_API%E6%96%87%E6%A1%A3) 未取得可读字段正文，搜索到的第三方转载没有作为口径定论来源。R1 需保存实际核验材料；原报告的“全为累计/实测非百分数”仍属待逐字段复核项。

迁移兼容：保留原 `period_kind` 以读取历史记录，新增字段作为新计算入口；旧 ambiguous cumulative 不自动推断。严格派生不支持的记录返回缺口，不能抛错拖垮整只股票分析。SOURCE/SCHEMA/CACHE 版本分别升级，旧缓存不可伪装新语义。

## 4. claim 从“格式合法”到“内容有依据”

现有 verify_claim 保留作结构检查的底层功能，调用方改消费分级核验结果：

| 状态 | 保证 | 能否直接满足关键事实要求 |
|---|---|---|
| PARSED | JSON/字段合法 | 否 |
| SOURCE_RESOLVED | 文档级引用、hash、实体均匹配 | 否 |
| EXCERPT_GROUNDED | 页/段/字符区间定位准确，原文片段确实存在 | 仅供进一步检查 |
| FACT_CHECKED | 主体、否定词、数值/单位、时间/阶段与原文一致，有核验方法记录 | 可按命题所需的证据等级引用 |
| NEEDS_REVIEW / REJECTED | 无法核实 / 已发现错误 | 否；保留原因 |

实现要求：

- `verify_claim(..., evidence_pool, as_of)`：历史调用强制传截止时点；无证据库不产生 citation_resolves。无时间不声称 no_future_date 已证实。
- `SourceDocument` 带 canonical_uri、正文 hash、security_ids、发表/版本时点、正文或定位到归档对象。标题不能冒充已读取全文。
- Claim 增加 `quote_span`/`quote_text`、事实阶段（意向/签约/交付/收入确认）、否定/条件标志。数值转换须给公式和量纲，不凭 whitelist 就接受。
- quote 包含并不代表推论正确。确定性校验覆盖抽取型数字与简单事实；复杂叙述需独立核验或人工核实，仍不能把第二次 LLM 回答视为真值 oracle。未达等级保持 NEEDS_REVIEW。
- “支持/反驳哪条逻辑”是**研究推论**，与原始事实分开存。例：有订单是事实，但金额、利润贡献、兑现时间、已计价程度共同决定是否支持某周期逻辑。
- 明确否定、单位错 100 倍、别家公司、框架协议当已确认收入、修订前后混用、文档里的指令文本，均纳入对抗案例。
- 内部默认不能把“有反向文字”直接判投资逻辑 INVALID。只有已核实事实满足具体失效条件，才触发 INVALID；未核实重大反证进入 REVIEW_REQUIRED。

缓存键：document_version_hash + extractor_version + model + prompt_hash + schema + verification_policy；核验派生缓存再含 as_of/逻辑版本。没有新文档不重复提取；换周期不重读同篇公告，但重新计算事实与该周期逻辑的关联。调用、重试、截断、拒识、成本均进同一 research_run。

## 5. 历史快照的最小存储

复用当前 JSON/JSONL 与原子写能力，首版不引入消息队列、图数据库或分布式工作流。建议在应用状态目录下增 `research/`（实现测试必须隔离 HOME）：

```text
research/raw/<sha256>.json                 原始响应/正文与采集元数据；不可覆盖
research/observations/<YYYY-MM-DD>.jsonl    观测索引；引用原件而非重复大正文
research/snapshots/<snapshot_id>.json       截止时点清单、版本、拒收原因
research/runs/<run_id>.json                 冻结输入、步骤、输出、成本、错误
research/invalidations.jsonl               数据纠错/源撤销及受影响对象
```

大规模逐日行情优先复用现有行情缓存，新增 manifest 描述股票全集、状态、复权/未复权语义和源版本；避免对每个 close 新建一份巨型 JSON。达到可测容量瓶颈后才引入 Parquet/SQLite 索引，独立登记依赖与迁移。

采集顺序：当前持仓/观察池所需资料 → 已选研究候选 → 全市场日快照。网络失败按现有超时/重试预算降级，任务账本记录到单数据分区，幂等恢复。写入必须 crash-safe；首次快照保存清单和缺失状态，不以空目录充当采集成功。

行业成员表建 `effective_from/effective_to`（经济归属生效）与 `known_from/known_to`（本系统何时知道该版本）双时间字段。只有当前查询值时从 first_seen 起提供前瞻能力；不可回填到上市日。公司行动、停牌/退市/ST 与交易规则亦引用有生效时间的版本。

行情 bar 的日期不等于天然 PIT：当下前复权历史会受以后分红拆股影响。成交、估值、收益计算各明确价格口径；以未复权成交价配公司行动台账计算持仓现金与数量，指标复权序列须保证仅包含当时已知调整因素。

## 6. 需要锁定的结果

R1/R2 的验收必须实际证明：latest-only+旧pubDate 被 strict 拒绝；历史原始公告可按其真实公开时点入选；后发修订不污染旧快照；比率不能累计差分；单位异常不自动纠偏；无正文/正文相反的 claim 不进入 FACT_CHECKED；当期可用数据仍能正常服务 live。详见 [TASKS.md](TASKS.md) R1/R2 与 [VALIDATION.md](VALIDATION.md)。
