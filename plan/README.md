# 暮云持续迭代计划

更新：2026-09-24。接手基线：`e84fac4`（v0.8.12.1）。

总架构、模块边界、能力增强路线和 Agent 协作机制见 [总体架构与演进设计](ARCHITECTURE.md)。首批技术任务卡、测试失败分析及读入顺序见 [技术交接](TECHNICAL_HANDOFF.md)。

这是跨 Claude Code / Codex / 其他 Agent 的计划与交接入口。**用户最新要求：本任务只写计划，不改代码。** 下表是供后续执行者认领的工作，不代表本任务正在实施。历史产品问题仍以 `ISSUES.md` 为索引；未来实施范围由接手任务的用户指令决定。

**角色约定**：当前任务担任总架构师，负责规划、技术设计、任务拆分、审查验收与后续迭代；执行 Agent 负责实现。目标是更优雅、更可靠、能力更强、代码更精简，具体判据见架构总纲。新增设计建议与已核实事实分别标注，不把设计当成实现。

## 目标与边界

先保证数据不丢、失败可理解、验证可复现，再缩小模块职责并优化日常工作流。成功指标是命令的实际行为及回归证据，不是删了多少行。

- 保持现有 REPL 命令和 chat 工具协议兼容。
- 研究/决策辅助定位不变；不接实盘交易，不用当前公告/基本面补历史回测。
- 评分公式、气宗/剑宗、Weinstein 双口径、force_exit 行为有既有回测约束，独立立项后才改。
- 保留用户工作区：`portfolio.yaml`、Claude 配置/计划、知识索引备份、未跟踪教材及临时研究资产。禁止目录级暂存。
- 当前记忆里的“WorkBuddy 只负责审查”属于旧角色分工，不限制本次明确授权的开发工作。

## 已核实的架构

```text
start.py parse_input / run_cli ─┬─ src/cli/main.py 分析、扫描、复盘、持仓
chat/tools.py run_command ─────┘
chat/tools.py analyze_stock ───── Orchestrator
                                  ├─ Skill → Decision → Event → AI Modifier
                                  ├─ EntryExit → Strategy → PlanGuard → Execution
                                  └─ 持仓状态回写 / WATCH 自动入池
scanner ── session_state ── last_scan / scan_history / watchlist ── review
RAGService ── ingestion / embedding / FAISS / retrieval ── CLI、chat、TradePlan
BacktestEngine ── DataFeeder ── Orchestrator(is_backtest=True)
```

2026-09-24 机械盘点：`src` 99 个 Python 文件，36,455 行；`src/cli/main.py` 4,395 行；`AGENTS.md` 395 行 / 63,836 字节。行号会变化，接手时重新搜索符号。

已读取：AGENTS / CLAUDE / CODEX、ISSUES 当前表、`.learnings` 近期记录、Claude 项目 memory 索引及条目、WorkBuddy memory 索引、旧重构与测试体系交接、现有 Claude 体验计划。记忆资产存在状态漂移，例如指标数学验证已通过 `test_indicator_math_script.py` 回归，不能照旧报告重复立项。

## 里程碑与验收

| 编号 | 状态 | 范围 / 技术方案 | 验收与可感知结果 |
|---|---|---|---|
| M0 | 已完成初步盘点 | 建立架构图、资产索引、实际测试基线；识别外源测试和副作用 | 已记录基线与边界；不声称完成全部源码逐行审计 |
| M1 | 待实施 | pytest 默认离线；真实接口显式开关；测试收集前隔离 home，保留离线 data_sources 单测 | 一条命令得到稳定结果；不碰真实 `~/.muyun`，不默认花 AI 费用 |
| M2 | 待实施 | 会话 JSON 快照同目录临时文件原子替换；验证 last_scan / deep_analyzed / 历史 / watch 读取边界；损坏行告警可见 | `last` / `#N` / `watch` 遇坏记录不崩，保留可读记录；写入失败旧快照完整 |
| M3 | 待实施 | 扫描报告命名防同分钟覆盖，兼容旧报告导入；故障注入及真实 REPL 冒烟 | 连续相同主题扫描保留两份报告，历史导入仍幂等 |
| M4 | 待实施 | 分批提取 CLI 的 review 纯计算与命令服务；main 保留兼容出口；共享入口只依赖服务，不反向依赖界面 | 精确数字及命令桥确认门不变；CLI/chat 相同输入同结果 |
| M5 | 待实施 | 持仓并发写保护：先梳理所有写入方法及调用方，再以内容版本检测拒绝旧快照覆盖；失败必须传到输出层 | 两实例修改不会静默覆盖，REPL/chat/Web/TUI 都不报假成功 |
| M6 | 待实施 | 集中运行诊断、缓存新鲜度与缺失原因呈现；核对安装入口；压缩 Agent 指令中的历史叙事 | 新环境可按文档启动，诊断不泄密；知识入口短而准确 |

执行顺序：M0 → M1 → M2 → M3；M4/M5 各独立提交与验证，M6 随批次收口。不得把“待实施”描述成已完成。

能力增强 C1–C6 与装配/数据质量/超时专项见架构总纲，等首批可靠性门槛通过后排入执行，不与 M1–M3 混做。

## 技术约束与风险

1. **测试**：不能按目录整组排除 `tests/data_sources`，其中大多数是纯 mock 测试。`test_all_api.py` 有 import 时环境/证书副作用，应在收集前排除，不能只靠 marker 事后筛选。外源体检保持显式可运行。
2. **持久化**：JSONL 保持追加式，不恢复 prune；快照原子替换不等于并发事务。暂不把所有缓存搬进新存储框架。不可修坏记录时直接重写原文件。
3. **数据兼容**：保留现有 JSON/YAML schema、`#N` 顺序、入池时间锚点和旧报告格式。告警新增同步 `plain_errors.py` 与报错速查手册。
4. **拆分**：先提取没有 IO 的 review 算法，再拆展示/命令。保留 `main` 的旧导入点及测试 patch 接口，确认 consumer 后再逐步迁移，避免一次移动整个大文件。
5. **安全网**：`position_action=CLOSE_ALL` 是执行语义；评分改动必须失效评分缓存；RAG 重排默认关闭；不并行加载多个真实模型。
6. **持仓**：当前 `_save` 发现外部修改只警告仍覆盖，多数 mutator 未传播 bool。M5 必须端到端设计，不能只在 `_save` 加一个 return 就声称修复。

## Agent 协作约定

- 接任务先读本文件、AGENTS、工程纪律技能与相关源码；旧文档只作线索。
- 在执行记录写清负责文件、状态与验收命令。避免同时编辑 `main.py` / `start.py` / AGENTS。
- 可并行的后续任务：M4 review 计算提取、M5 持仓调用链设计、M6 安装诊断设计；每项须明确文件所有权，与其他 Agent 的改动兼容，不回退他人修改。
- 修复先给回归红灯，转绿后扫同类点，教训进入 `.learnings`；运行与文档证据同批交付。
- 有全局状态改动必须跑完整离线回归。真实数据接口/AI 验证单独记账，不能用它们的抖动掩盖离线失败。

## 执行记录

### 2026-09-24 / Codex / 调研与计划交付

- 当前负责人：本任务仅负责调研与计划；M1–M6 尚无实施负责人。
- 已发现：无 `pyproject.toml`/pytest 配置、测试说明漂移、会话快照非原子写、读取只验证 JSON 语法而未验证数据结构、同主题报告以分钟命名可覆盖。
- 原代码基线：排除 `test_all_api.py` 与 `test_check_all_sources_structure`，临时目录隔离 HOME / USERPROFILE 后，**741 passed / 3 failed / 2 skipped / 1 deselected，114.02 秒**。这不是含真实外源的全量验收。
- 失败项：恐慌指数 STALE 回退、恐慌指数快照日期、PlanGuard 止盈路径，详见配套技术交接。未改断言或业务逻辑来消除失败。
- 范围纠正：用户明确只要计划后，已撤回试作的 `pytest.ini`、`tests/conftest.py` 修改与新增隔离测试，结束后续测试运行；无 `src/` 改动、无 commit/push。试作测试配置的结果不作为产品基线，教训见技术交接。
- 交接：后续执行者实施时更新上表及记录；本任务交付的是计划，不是 M1–M6 的完成声明。
