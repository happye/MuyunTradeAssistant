# codex-dsh 工具改进需求（来自暮云项目，2026-10-28）

- 对象：独立工具仓库 [happye/codex-dsh](https://github.com/happye/codex-dsh)（研究版本 0.1.4，`package.json:3`，HEAD `b2efefd`）。
- 性质：**需求与问题报告，供工具项目评估**。按暮云台账治理规则，协作工具开发不在暮云范围内（`PROGRESS.json` governance.out_of_scope），暮云不实施这些改动；暮云侧的应对办法写在 [WORKFLOW.md 第 8 节](WORKFLOW.md#8-dsh-使用规则codex-机器)。
- 方法：只读阅读工具源码与手册（未运行 node/npm、未连接 Host、未读私有账本）。下表“未复现”的条目是代码推断，需工具项目用测试复核。下文路径均相对工具仓库根。

## 1. 问题（按对暮云的影响排序）

| # | 问题 | 依据 | 对暮云的影响 | 状态 |
|---|---|---|---|---|
| K0 | 合同绑定执行目录的 HEAD/分支，提交后同一 task 的 send/run/receipt/readiness 与修订全部失败；同一 state-dir 一个会话只能绑一个 task | `src/contract.mjs:151-152,181,189,205,125`；`src/controller.mjs:71,218-220` | 每次提交都得新开 task，迫使“每卡一 task、卡越切越小” | 设计如此，需求见 Q1 |
| K1 | stop 只能置位不能撤销；“静止”判定要求整段会话从未出现工具活动，有过工具调用的会话到不了 STOPPED | `src/controller.mjs:421,485-488,98`；`src/events.mjs:70` | 每次额度停止都留下永久 STOP_PENDING，只能“保留锁、不重投、不杀 Host” | 未复现，需测试复核（现有 STOPPED 用例事件流里无工具调用：`test/controller.test.mjs:173-182,655-661`） |
| K2 | SUBMITTING 无历史匹配即转 SUBMISSION_UNKNOWN；未决请求与 UNKNOWN/STARTING 执行意图永久阻断同一 task，没有清除路径 | `src/controller.mjs:106-108,203`；`src/contract.mjs:10,148,229,262` | task 作废只能换新 task，交接文档里堆积 request/stopAttempt 身份 | 测试把阻断固化为预期（`test/controller.test.mjs:412-418`） |
| K3 | stop 无法中断运行中的测试：CLI 不传 signal；stop 等 dispatch 锁 20s，而 run 持锁最长 300s；失败时诊断可能误记 STOP_WATCH_READY_FAILED | `src/cli.mjs:114`；`src/contract.mjs:201,242,267`；`src/controller.mjs:497,504` | 长测试期间无法干净停止 | 未复现 |
| K4 | 锁无陈旧检测；`state.lock` 为全局锁，残留即整个 state-dir 不可用 | `src/store.mjs:65-80,86-88` | 进程异常退出后需人工判断删锁 | 未复现 |
| K5 | 每次 refresh（含 send）全量重读整段会话历史 | `src/native.mjs:227-238`；`src/controller.mjs:195` | 长会话越来越慢（未测量） | 未测量 |
| K6 | 全树指纹覆盖全部被跟踪与未忽略的未跟踪文件，执行目录内任一范围外改动即 CONTRACT_FILE_SCOPE_VIOLATION，旧回执 valid=false、readiness 失效 | `src/execution.mjs:51-62`；`src/contract.mjs:155-156,184,197,213` | 主控改文档也会让测试回执失效，导致反复全量 | 设计如此，暮云用独立代码工作树规避 |
| K7 | `runtimeInputs` 须逐文件列全外部依赖，否则回执不可复用、passed 不会为 true | `docs/manuals/contracts.md` 测试计划段 | Python 项目要列解释器与全部 site-packages 元数据，暮云曾因此手工生成 1.5MB 源指纹清单 | 需求见 Q4 |
| K8 | profile 与 state-dir 含本机绝对路径与凭据，task 记录目录 dev/ino 身份，跨机器不可迁移；合同 gitOwner 硬编码为 `'Codex'` | `src/setup.mjs:64-112`；`src/identity.mjs:6-31`；`src/contract.mjs:48` | 另一台机器（如 Claude Code 主控）无法接手同一 task，只能各自起任务 | 设计如此 |

## 2. 需求（建议优先级）

| # | 需求 | 解决 | 验收建议 |
|---|---|---|---|
| Q1 | **受控推进基线**：主控提交后，允许对同一 task 执行显式的“基线前移”（新 HEAD 必须是旧 HEAD 的后代，且新增提交只含已验收改动），保留历史回执的可追溯性 | K0：一个 task 可覆盖一个阶段的多次提交，不再被迫每提交换 task | 反例：前移到非后代提交、前移时含范围外改动、前移后旧回执被误当当前通过 |
| Q2 | **受控清除 stop / 人工裁决未知状态**：在主控提供“已核实无副作用”的书面理由后，把 STOP_PENDING、SUBMISSION_UNKNOWN、UNKNOWN 意图转为可审计的终态（如 STOPPED_BY_ADJUDICATION），原始记录不删 | K1、K2：额度交接后不再遗留永久阻断 | 反例：无理由清除、清除后重投同一未知请求、清除覆盖原始日志 |
| Q3 | **stop 可中断运行中的测试**：stop 向 run 的直接子进程传递取消，锁等待与 run 超时对齐，诊断分清“等锁失败”与“观察失败” | K3 | 长测试中 stop 的终态与诊断正确 |
| Q4 | **runtimeInputs 支持清单摘要**：允许以“解释器路径 + 发行版 RECORD 清单文件”或锁文件摘要代替逐文件枚举，摘要变化即回执失效 | K7：Python 项目不必生成巨型逐文件清单 | 改动任一已安装包的 RECORD 使回执失效；未声明依赖仍不能 passed |
| Q5 | **陈旧锁检测**：锁文件记录持有进程与时间，持有者已退出时给出明确诊断与安全接管路径（不自动删除） | K4 | 模拟持有者退出后的诊断 |
| Q6 | **增量历史读取**：refresh 只读取 cursor 之后的新事件 | K5 | 长会话下 send 耗时不随历史线性增长（需基准） |
| Q7 | **只读交接导出**：导出 task 的可公开摘要（task/session、合同 sha、最后请求终态、未决状态、回执 runId 列表），不含凭据，供另一台机器或另一种主控只读核对 | K8：仓库文档不必再手抄运行态身份 | 导出不含 cookie/token；导入方不能据此驱动会话 |
| Q8 | **gitOwner 可配置**（如 Codex / Claude Code），仍保持“只有主控提交、实施方不提交” | K8：用户改派主控时不必绕开工具 | 非允许值拒绝；实施方提交仍被阻断 |

## 3. 暮云侧已采用的规避办法（不依赖工具改动）

见 [WORKFLOW.md 第 7–8 节](WORKFLOW.md#7-额度与中断)：执行目录改为仓库外的 detached 专用代码工作树；一卡一 task，切片验收后由主控在主检出提交（工作树 HEAD 不变，task 不断链）；不用 stop 做额度交接；测试改用工具自带 testPlan/run/receipt 并加 `--output-budget-bytes 16384`，仓库只记 runId 与结论；机器运行态不进仓库。

## 4. 转交方式

由用户决定是否把本清单转给工具项目（例如作为该仓库的 issue 或 `docs/validation.md` 的候选条目）。暮云侧不直接向工具仓库提交。
