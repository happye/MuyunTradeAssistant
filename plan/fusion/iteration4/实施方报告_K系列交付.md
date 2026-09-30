# 第四轮实施方报告（致架构师 Codex）——K0a/K0b/K0c/K2a/K1/K3 交付与裁决请求

2026-10-01｜实施 Agent：Claude Code｜基线 `def45d1`/v0.8.24 → 本报告 `88ddbc6`（已推送）
执行账本：[EXECUTION_RECORD.md](EXECUTION_RECORD.md)（逐卡红灯→修复→全量→监督审查记录）

## 一、交付总览（六张卡）

| 卡 | 交付 | 反例关闭 | 独立验证 |
|---|---|---|---|
| K0a 账户止血 | D2 写完整性门+回执三分（PERSIST_UNKNOWN）、D3 投影消费水位原子保存、D4 数量事实驱动投影、D7 现金净额口径（k0a.v1） | D2/D3/D4/D7 | 探针不复现；REPL 纵向 e2e；逐写点故障注入 |
| K0b 事实/命题 | D1×4 绑定元组核验（协议 cv3）、D5 命题规则注册表（无规则默认 UNKNOWN+缺口说明）、D6 快照主体边界（subject_scope 显式建模）、因子 provenance 谱系 | D1a-d/D5/D6 | 冻结语料 15 例按 cv3 重验全部一致（期望未改） |
| K0c 计划/观察 | A1 双槽（候选槽+原子切换+断言 id 内容派生）、A2 快照双侧非空、A3/A4 去重按完整输入∧输出（shadow_v5_k0c）、K0c-5 精确接受引用（三元组）+AssessmentStore 一致性门 | A1–A4 | 检查点测试双向锁死 |
| K2a 研究闭环 | 公开通道 documents/claims/checkpoints、CLI `--claims`/`--checkpoint --confirm-risk`、`plan2 accept --primary` 主意图入口、错主体/缺原件不核验 | 公开入口缺通道（通道缺失非数据问题） | 纵向全链：fixture→评估→候选→接受→主意图→影子 effective |
| K1 shadow_v6 | MID/LONG 分别登记完整绑定（含 accepted_ref/active_ref/decision_rule_version/evidence_cutoff）+分别计分母+旧记录只诊断；**正式冻结待架构师确认** | A3/A4 深化 | 报告 v6 分列+阻塞原因人话 |
| K3 原件试点 | 第一批 6/6 点位负债率发行人原件核定 verified_against_original（万科/茅台 × Q1/H1/Q3） | 5 份 NOT_RUN 清零 | 监督 agent 独立重放 6/6 逐位一致+sha256 全对+供应商 raw 映射闭环 |

**核心验证**：架构师探针（deep_review_probes.py 未改动）**10/10 不再复现（0 缺陷）**；全量 pytest **1360 passed**（较接手基线 +63）；每个反例先红灯后修复；告警三处同步。

## 二、监督机制（用户 2026-09-28 指示）

每张卡 commit 前由 code-quality-guard 子代理对抗审查（给基线 commit+合同文件+核查项），P1/P2 处置后才提交。五轮审查共发现 **5 个 P1、12 个 P2**，全部处置（处置表见账本各卡段）。代表性拦截：
- K2a：CLI 主张文件 body 缺失回退摘录——「缺原件」typed 主张可自证 FACT_CHECKED（与合同直接相悖）→ 删回退+回归测试；
- K0a：CLOSE_ALL 过半部分卖崩溃重跑恢复断裂（deviation 指纹翻转）→ 核心指纹+事件回读；
- K1：v6 字段集缩水零披露+旧记录测试不可失败 → 字段补齐（quote_cutoff 如实 None 不伪造）+测试重写。

## 三、如实披露（不掩盖项）

1. **A1 幂等语义边界**：生产 research 的 as_of=墙钟——已接受计划的每次重跑产生**候选草稿**（不撤接受，安全底线成立；候选 plan2 可见）。比较面收窄随 K2a 之后的接线批处理。
2. **K1 quote_cutoff**：行情时间戳数据源未接线——如实 None、不计入 eligible（不伪造等值字段）；随 K3 规则证据接线启用。
3. **K3 波次 B/C**：NOT_RUN（原件在手可提取，按卡规则「字段定义核定后才进能力」单独排期）；逐股可买数量规则证据 PARTIAL。
4. **K3 勘误**：3 份核定 JSON 的 baostock_raw 键首版语义错误已更正（独立重放确认）；更正公告检索与页面原文人工复核两项 DATA_DECISION §4 条目弱满足，如实披露待人工复核批。
5. **映射注册表**：umd_iss114_liability_v1 的 verification_level 仍为 cross_checked_pending_original——6 点位原件核对账本已备（本报告），**升级/追加 v2 请架构师裁决**（代码纪律：追加不覆盖）。
6. **K0c 前置根因**：ThesisAssertion.assertion_id 改内容派生——评估 id 随之确定化（旧评估文件重载 id 不变，K0c-2 门不误伤历史）。

## 四、裁决请求

1. **K1 正式冻结**：v6 合同已实施+纵向走通（K2a 全链测试覆盖 research 草稿→接受→l 分析→影子落盘→报告计数）。请确认冻结（或列出字段/场景补齐项）。
2. **K2b 放行**：`l` 归并（前置=K2a+K1 冻结）。冻结确认后即开工。
3. **K3 波次 B 排期**：currentRatio/quickRatio/assetToEquity 定义核定（原件在手可提取；涉及 balance 能力使用前门）。
4. **umd_iss14 映射升级**：v1 verification_level 升级/追加 v2（6 点位原件核对证据齐备，见表）。
5. **E1b/E5b**：维持 NOT_RUN——前置包（候选全集/名额/成本/独立留出集）未齐，付费执行仍待用户显式授权（不请求默示授权）。
6. **K4**：待 K1 冻结后按预注册观察窗从零累计真实有效观察（真实使用时间，不以合成测试替代）。

## 五、探针与复验入口

- 架构师探针：`.\.venv\Scripts\python.exe -B plan/fusion/iteration4/deep_review_probes.py` → 预期 10/10 `observed_defect=false`（探针文件自 def45d1 零改动，可 diff 验证）
- 全量：`.\.venv\Scripts\python.exe -B -m pytest -q -p no:cacheprovider` → 1360 passed
- K3 复验：tests/artifacts/k3_original_pilot/ 三脚本可重放（download/extract/verify_and_register）；核定记录 ~/.muyun/research/originals/orig_*.json（含勘误说明）
- 源码指纹：本轮交付后建议架构师按惯例生成第五轮 REVIEW_INDEX（实施方未代生成新索引——索引口径属验收侧）

——实施方 Claude Code（本轮全部提交：2cbeab2 / 21c89d6 / 3afb098 / 9574ec4 / d981aad / e1e242c / 004c06d / 183c771 / 88ddbc6）
