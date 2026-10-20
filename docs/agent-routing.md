# 子 Agent 按需分工

这是用户的持续分工要求。主控应主动执行，不等每次提醒；`.codex/config.toml`和`.codex/agents/*.toml`规定模型及职责，任务是否派发由实际读取的AGENTS.md与当前用户要求决定。主模型由用户选择，不修改。

## 什么时候派任务

每项实质任务先做一次判断：能否把独立定位、文档整理、边界明确的实施或必要复核交给子Agent，减少主上下文或提高验证质量？能就派最少所需角色，并在派发前一句话说明任务和型号。短步骤、相互依赖的工作、低影响单文件修改直接完成；不凑人数、不多派相同审查、不轮询未变进度。

实质里程碑先确定范围与验收条件，再按依赖安排实施和独立复核；主控可完成规划，不强制为每个步骤另派Agent。写任务明确文件所有权，所有参与者保留他人未提交成果；默认不递归。只读任务不修改或触发外部操作；阶段门禁及费用授权继续有效。

## 模型路由

| 任务 | 角色 | 模型 / 推理强度 |
|---|---|---|
| 定位代码、资料盘点 | task_explorer | gpt-6-luna / high |
| 进度摘要 | task_progress_monitor | gpt-6-luna / low |
| 普通文档整理、未命名有界任务 | default/worker | gpt-6-luna / medium |
| 实施、普通审查 | task_developer / task_reviewer | gpt-6.1-sol / medium |
| 数据、费用、持久化、并发、身份关键边界 | task_quality_guard | gpt-6.1-sol / high |
| 复杂架构设计 | task_architect | gpt-6-astra / low |
| 专项规划、生成、独立评估 | harness_planner / harness_generator / harness_evaluator | gpt-6.1-sol / medium |

以上组合在本会话可用枚举内，不等于每个账号或客户端都支持。明确型号覆盖采用最小任务上下文；当前编排工具要求`fork_turns="none"`，完整历史fork会继承主模型。角色不可发现时用支持的default/worker，显式指定model/effort及职责，不替换成绑定其他厂商模型的预设。不支持或调用失败先如实说明，不能默默换回主模型。小模型不足时带证据升级一次，不反复尝试多轮。

## 直接触发的指令

在聊天框发送：

> 按需分工：<任务内容>。先实际派出适合独立完成的子任务，按AGENTS.md选择模型；主控整合结果。模型或工具不可用立即说明，不默默用主模型代做。

这是明确的聊天指令，不是Codex内置斜杠命令。全局和项目AGENTS.md均已将“按需分工：”定义为分工触发语。简短任务无需强行拆分，主控应解释一次。新的“本项不用agent”可临时覆盖，不能延续成后续任务禁令。

OpenAI官方说明：当前本地Codex在用户明确请求，或适用AGENTS.md/skill要求时分工；模型默认值只决定派出后的模型，显式spawn设置优先。见[Subagents](https://developers.openai.com/codex/subagents)与[Config reference](https://developers.openai.com/codex/config-reference)。本机CLI的`/agent`用于查看/切换已派出的任务，不是自动派发开关。

## 配置与实际执行验证

本机原生配置已经通过`codex-cli 0.162.0-alpha.17.2`的strict-config/app-server读取检查：agents启用、默认Luna/medium、并发3；未改父模型字段。早期会话曾不识别task_explorer，因此保留default/worker显式覆盖兼容方式，不宣称所有客户端热加载成功。

2026-10-13本次真实文档任务的执行元数据：

| 实际任务 | 请求与turn_context一致的模型 / effort | 运行会话ID |
|---|---|---|
| delegation_diagnosis（只读定位） | gpt-6-luna / high | 01a13101-05cf-7882-9a2a-a1f6c8e24c9d |
| iss117_handoff_cleanup（交接整理） | gpt-6-luna / medium | 01a13100-b5ff-77d1-9e5b-d4373c8e7233 |
| iss117_record_cleanup（验收合同删减） | gpt-6.1-sol / medium | 01a13100-4dd5-7fb0-8ad7-e24d0a6b5ad7 |

证据从本机会话JSONL的session_meta与turn_context读取，未采用Agent自报型号。原生检查/安装备份仍在gitignored的`.cache/harness-agent-routing/`；本次只补主动调度规则，未改个人或项目config.toml。Astra及其他未实际执行组合不冒称实测；没有测算额度节省比例。下一新任务是否主动派发仍应看工具调用与活动记录，不能用配置或文字承诺替代。
