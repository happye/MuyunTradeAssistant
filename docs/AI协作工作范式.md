# Muyun AI协作工作范式

这份文档把当前仓库已经验证有效的 AI 协作资产整理成可提交、可迁移、可复用的工作范式，目标是让另一台机器在克隆仓库后，也能复用同一套 Copilot 工作方式。

## 1. 资产分层

### 1.1 仓库级常驻规则

- 文件: `.github/copilot-instructions.md`
- 作用: 定义这个仓库里“几乎所有任务都适用”的行为规则，例如搜索起点、验证方式、失败处理、文档职责划分。
- 官方依据: VS Code 会自动发现工作区根目录下的 `.github/copilot-instructions.md`，并将其应用到该工作区内的所有聊天请求。

适合放在这里的内容:

- 项目范围内通用的代码边界
- 测试/验证命令
- 必须遵守的 workflow 规则
- 明确高频踩坑和禁区

不适合放在这里的内容:

- 只适用于某一种文件类型的规则
- 很长的操作手册
- 只在个别任务中才需要的步骤

### 1.2 按需加载的工作流能力

- 目录: `.github/skills/self-improvement/`
- 作用: 封装“何时触发、如何执行、引用哪些资源”的多步骤工作流。
- 当前仓库的核心用途: 把“错误记录、经验沉淀、用户纠偏、能力缺口”变成结构化流程，而不是临场口头承认。

官方依据:

- Copilot 将 skill 视为按需加载的任务型能力，不同于 always-on instructions。
- Skill 目录至少包含 `SKILL.md`，并且可以携带 `scripts/`、`references/`、`assets/` 等资源。

### 1.3 项目级“错题本”/经验账本

- 目录: `.learnings/`
- 文件:
  - `.learnings/ERRORS.md`
  - `.learnings/LEARNINGS.md`
  - `.learnings/FEATURE_REQUESTS.md`
- 作用: 记录这个项目里已经发生过的错误、沉淀下来的稳定经验、以及未来能力请求。

推荐分工:

- `ERRORS.md`: 记录失败和复现条件
- `LEARNINGS.md`: 记录稳定规律、纠偏结论、最佳实践
- `FEATURE_REQUESTS.md`: 记录仓库当前还没有、但明确被需要的能力

### 1.4 本地私有记忆

- 目录: `.workbuddy/`
- 当前状态: 已在 `.gitignore` 中忽略
- 作用: 本机私有的临时工作记忆，不适合作为团队共享资产

结论:

- `.github/` 和 `.learnings/` 适合进版本控制
- `.workbuddy/` 适合继续保留为本地私有层

## 2. Copilot 的参考机制

这一节只区分两层事实: “官方明确说明的机制” 与 “业内通用但不是 Copilot 官方承诺的解释”。

### 2.1 官方明确说明的部分

#### A. `.github/copilot-instructions.md` 是 always-on

根据 VS Code 官方文档，根目录下的 `.github/copilot-instructions.md` 会被自动发现，并应用到工作区内的所有 chat 请求。

这意味着:

- 它不是按关键词才生效
- 它不是只在编辑某些文件时才生效
- 它更适合放“项目级通用规则”

#### B. Skill 不是全文索引整个仓库，而是“目录化能力包”

根据 VS Code 官方文档和 Agent Skills 公开规范，一个 skill 的最小单位是一个带 `SKILL.md` 的目录。可选资源包括:

- `scripts/`
- `references/`
- `assets/`

这意味着 skill 的可发现范围首先是“这个 skill 目录自身”，不是任意扩展到整个仓库。

#### C. Skill 的发现和加载是 progressive loading

Copilot 官方文档明确写了三段式加载:

1. Discovery: 先读 `name` 和 `description`
2. Activation: 任务匹配后再读 `SKILL.md` 主体
3. Resource access: 只有在 `SKILL.md` 里引用到的额外文件，才会按需加载

因此，skill 的 `description` 本身就是路由入口；写得越具体，越容易被自动匹配。

#### D. Skill 里的额外文件不会自动全读

Copilot 官方文档明确说明: 如果附加文件没有被 `SKILL.md` 引用，它就不会被加载。

因此:

- skill 不是一个“自动索引所有同目录文件”的黑盒
- `references/`、`scripts/`、`assets/` 需要被 `SKILL.md` 明确引用
- 写在 skill 目录里但没被引用的资料，不能假设 Copilot 一定会看到

#### E. Skill 可以带脚本，但脚本是否执行仍受 agent 工具和审批约束

官方文档只说明 skill 可以携带脚本与资源，并在执行中被使用；是否真的运行这些脚本，仍取决于当前 agent 有哪些工具、当前环境是否允许执行、以及终端审批策略。

所以 skill 的本质是:

- 封装工作流和资源
- 不是绕过工具权限的后门

### 2.2 业内通用、但不应伪装成 Copilot 官方承诺的部分

下列判断更适合作为“当前主流 agent 生态的通用范式”，而不是 Copilot 官方硬保证:

1. 用 metadata 做轻量路由
2. 用延迟加载控制上下文成本
3. 用 references/scripts/assets 把能力包模块化
4. 用 repo-level instructions 放通用规则，用 skill 放特定工作流

这套思路不只出现在 Copilot。Agent Skills 公开标准本身就把 skill 定义成一种可移植、按需加载、目录化封装的能力单元。

但要注意边界:

- “通用范式”不等于每个 agent 的实现细节都一样
- 具体是否支持 slash command、forked context、allowed-tools、自动发现目录，仍然要看具体产品文档

## 3. 当前仓库的推荐方法论

### 3.1 一条主线，三层资产

建议把 Muyun 的 AI 协作长期固定成以下三层:

1. `.github/copilot-instructions.md`
作用: 放项目级共识和全局规则

2. `.github/skills/self-improvement/`
作用: 放可复用的“自我提升/错题复盘”工作流

3. `.learnings/*.md`
作用: 放项目运行过程中产生的具体错误、经验、能力请求

对应关系是:

- instructions 决定“默认怎么干”
- skill 决定“遇到某类问题时按什么流程干”
- `.learnings` 记录“这个项目里已经踩过哪些坑、形成了哪些稳定规律”

### 3.2 什么该提交到 GitHub

建议纳入版本控制:

- `.github/copilot-instructions.md`
- `.github/skills/self-improvement/`
- `.learnings/ERRORS.md`
- `.learnings/LEARNINGS.md`
- `.learnings/FEATURE_REQUESTS.md`
- 本文档

建议继续保持本地私有:

- `.workbuddy/`
- 任何包含账号、密钥、临时终端输出、原始日志的文件

### 3.3 新机器上的复用方式

新机器克隆仓库后:

1. 打开仓库
2. 确认 VS Code / Copilot 会自动发现 `.github/copilot-instructions.md`
3. 确认 `.github/skills/self-improvement/` 被发现为 workspace skill
4. 继续沿用 `.learnings/` 作为项目级错题本

这套方式的好处是:

- 不依赖某一台机器的本地记忆目录
- 规则和经验可以跟仓库一起迁移
- 新会话、新机器、新代理都更容易继承同一套方法

## 4. 当前落地建议

对 Muyun，建议把 AI 协作资产固定成如下提交单元:

1. 提交 `.github/copilot-instructions.md`
2. 提交 `.github/skills/self-improvement/`
3. 提交 `.learnings/` 三个基础文件及后续无敏感内容的条目
4. 不提交 `.workbuddy/`

## 5. 参考依据

- VS Code 官方: `Use custom instructions in VS Code`
  - https://code.visualstudio.com/docs/copilot/customization/custom-instructions
- VS Code 官方: `Use Agent Skills in VS Code`
  - https://code.visualstudio.com/docs/copilot/customization/agent-skills
- VS Code 官方: `Customize AI in Visual Studio Code`
  - https://code.visualstudio.com/docs/copilot/customization/overview
- Agent Skills 开放规范
  - https://agentskills.io/
  - https://agentskills.io/specification