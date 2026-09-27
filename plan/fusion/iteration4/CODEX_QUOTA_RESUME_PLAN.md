# Codex 额度刷新后续跑：可执行方案与边界

2026-09-27｜仅适用 Codex 本机运行，不改变 Claude Code 的角色或调度。**用户本次选择：只交付方案，继续深审项目；未部署任何守护进程、计划任务或自动续跑。**

## 能保证什么

模型请求被额度墙拒绝后，不能依赖该模型请求继续运行代码来等待刷新。需要一个独立于推理的调度器保存任务状态、读额度、等到可用再续跑。它能在机器在线、登录有效、服务可用、任务可继续时自动尝试；不能保证每个刷新窗口必定成功，也不能绕过周限额、账号权限或服务故障。

Codex存在五小时窗口，也可能受周额度影响；实际窗口与刷新时点以使用量面板/接口为准，不能简单从进程启动时间每隔五小时重试。[官方额度说明](https://learn.chatgpt.com/docs/pricing)

## 本机已验证

- CLI版本：`0.158.0-alpha.2.1`。
- `codex exec resume --help` 支持指定 SESSION_ID 续跑、JSON事件及结果文件。未执行续跑，避免在当前活跃会话里启动第二个执行者。
- 临时启动 `codex app-server`，完成 initialize/initialized，只调用 `account/rateLimits/read` 成功；结束后已关闭该探测进程，没有启动模型回合。
- 查询当时返回 `ordinaryUsageAllowed=true`；五小时窗口usedPercent=45、刷新2026-09-28 03:11:56 +08:00；周窗口usedPercent=20、刷新2026-10-04 15:37:41 +08:00。**这是一时快照，后续不得硬编码这些百分比或时间。** 未落盘账号标识、认证内容。
- 当前会话未提供自动化/线程管理工具；无法在此直接创建官方计划任务。

查询协议来自[官方 App Server 文档](https://learn.chatgpt.com/docs/app-server)，本地握手和额度读取已实测；恢复跨桌面/CLI会话的行为仍需部署前验收。

## 推荐落地：本机外部调度器

独立小程序（Python或PowerShell）由 Windows 任务计划程序在登录后启动，隐藏窗口，单实例，保留本地状态。不要让模型每分钟发请求来问自己额度够不够。

### 1. 固定任务与恢复目标

保存以下状态在用户自己的 Codex运行目录，**不要写入共享AGENTS或暮云产品配置**：

```json
{
  "thread_id": "部署时核实的精确会话ID",
  "workspace": "G:/Tools/暮云思辨投资助手",
  "task_state": "waiting_quota",
  "last_attempt_id": "",
  "last_turn_id": "",
  "next_check_at": "",
  "resume_prompt_file": "本机固定恢复提示文件",
  "allowed_scope": "架构审查与任务卡；不改产品代码或真实账户"
}
```

目标必须明确且尚未完成。当前审查交付完成后，如果只在等Claude提交实现，就转 `waiting_implementation`，不在额度刷新时无目的地重审或重跑测试。用户暂停、等待回答、已完成，都不自动恢复。

### 2. 查询与等待

持久 stdio app-server 连接仅使用额度和状态读取接口；每次连接先握手：

```json
{"id":1,"method":"initialize","params":{"clientInfo":{"name":"quota_watchdog","version":"0.1.0"}}}
{"method":"initialized","params":{}}
{"id":2,"method":"account/rateLimits/read","params":{}}
```

处理 `rateLimitsByLimitId` 中与目标模型相应的桶及primary/secondary窗口，同时看ordinaryUsageAllowed、rateLimitReachedType、spendControlReached。字段缺失/未知枚举时不能猜“可以用了”。

- 若阻塞窗口已耗尽，下一次查询时间取**所有当前阻塞窗口的最晚 resetsAt + 30秒**；睡眠期间无需模型参与。用系统计时器，在唤醒/网络恢复时补一次检查。
- 额度可用且确实还有工作时才准备一次续跑；额度读取本身不创建推理回合。
- 网络异常按1/2/5/15分钟退避；认证失效、未知计费阻塞和连续协议错误提示用户，不能反复登录或无限快速重试。
- 不自动消费额外credits、reset credits，不切换付费API或账号来绕过限制。

### 3. 恢复前排除重复执行

对指定会话取运行状态和最近turn，确认是额度错误中断；普通完成/用户输入等待不能当限额中断。调度器持有按thread_id的跨进程锁和attempt_id，在落盘“即将恢复”后再提交一次恢复。重启先核对最近turn，防止重复提交。

**关键待验项**：单独启动的app-server是否能可靠反映桌面进程的活跃状态，不能假定。部署优先使用可核实的同一后端/daemon；若做不到，使用专用CLI执行会话，并与桌面约定唯一执行者。不得直接写桌面SQLite或模拟点击“重试”掩盖并发问题。

已支持的手工恢复命令模板（SESSION_ID须替换为核实后的目标，不能用`--last`猜）：

```powershell
Get-Content -LiteralPath 'C:/Users/Crux/.codex/quota-watch/resume-prompt.txt' -Raw |
  codex exec resume '<SESSION_ID>' - --json --output-last-message 'C:/Users/Crux/.codex/quota-watch/last-result.txt'
```

部署实现优先用参数数组启动进程并通过stdin写提示，避免shell插值。沿用会话权限和模型，不传绕过批准/沙箱的开关。官方说明支持[按会话ID恢复非交互任务](https://learn.chatgpt.com/docs/non-interactive-mode)；本机只验证了命令存在，尚未证明桌面这个活跃会话能安全地被CLI接续。

建议恢复提示：

> 按本会话用户最后指派继续未完成工作。先读CODEX.md、plan/fusion/RESUME.md和STATUS.md，检查HEAD/工作树与最近交付。保持架构师范围，不改产品/真实账户，不重复已完成审查。用户等待/已完成/待实施方输入则停止。只在上次确为额度中断且仍有授权工作时续跑；遇额度再次保存当前进度。不得自动付费或切换账号。

### 4. 结果状态机

```text
等待额度 → 查询恢复 → 核对空闲及未完成任务 → 提交一次续跑 → 监听结果
    ↑                                                    │
    └────────────明确再次额度拒绝──────────────────────────┘
结果为完成 / 等用户 / 等实施方 → 停止自动续跑
结果为网络 / 认证 / 工具失败 → 分类处理，不能统一无限重试
```

不靠exit code=0判断用户目标完成；读取结构化turn状态和任务回执。只有真正配额错误进入quota等待，普通错误有有限重试和明确通知。没有状态变化不发“还在等”消息；恢复成功、无法恢复、需要用户时才通知。

## 官方计划任务作为简化选择

可以在 Codex 桌面/网页的Scheduled界面为已有聊天设置周期任务；本地任务依赖电脑开启及App运行，已有聊天任务会使用其上下文。[官方计划任务说明](https://learn.chatgpt.com/docs/automations?surface=app)

这能周期唤醒，但官方页面没有承诺“额度拒绝后按reset时间自动重试成功”。周期任务也可能在额度耗尽时失败；本需求优先选外部额度检查器，不能把“创建了定时任务”当成“额度恢复得到保证”。Goal可保存目标，也不等于订阅额度监视器。

## 部署前验收清单

1. 用模拟额度响应验证五小时耗尽、周耗尽、两者同时耗尽、字段缺失、网络与认证异常；不需要真的把额度烧完。
2. 在专用测试会话验证一次resume及上下文/权限保留；活跃会话不能被第二次提交，程序崩溃重启不重复恢复。
3. 完成/等待用户/等待实施方/用户暂停四种情况均停止；仅quota拒绝进入长等待。
4. 电脑休眠、网络断开后能补查，通知只在可行动变化时出现；日志无token、账号标识或完整会话隐私。
5. 这些通过后再绑定正式会话；当前用户选择只交方案，以上步骤本次不执行。
