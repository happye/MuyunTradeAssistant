# Chat 模块架构文档（Chat Agent）

> 撰写：2026-08-23 ｜ 适用版本：v0.8.8.1（v0.8.8 命令桥 + v0.8.8.1 个股数据缓存） ｜ 取代 `2026-07-21_Chat功能说明.md`（v0.8.1 时代，已移入 [archive/](archive/2026-07-21_Chat功能说明.md)）
> 定位：让读者在 15 分钟内完整理解 chat 模块的**功能边界、架构分层、核心循环实现、可靠性设计、数据来源与已知局限**。
> 源码共 5 文件 ~2100 行：`src/chat/{agent,tools,prompts,formatter,__main__}.py`

---

## 一、它是什么（一句话 + 设计哲学）

**Chat 是一个自然语言 Agent 入口**：用户用中文提问，LLM（DeepSeek-V4 / Kimi）作为调度大脑，通过 OpenAI Function Calling 自主决定调用哪些底层分析引擎，再把多份结构化结果综合成一段人话回答。

三条设计哲学，贯穿所有实现细节：

1. **编排层，不是新引擎**：chat 自己零业务逻辑，全部复用 CLI 同款的 `Orchestrator / ScannerEngine / PortfolioManager / NewsClient / RAGService / industry_data`。保证"chat 说的"和"命令行算的"是同一套代码算出来的（历史上曾因漏传参数导致两者分歧，见 §六 H1 修复）。
2. **纯文本世界**：CLI 层用 Rich Console 直接打印、无返回值，chat 绕过它直接调底层引擎；所有工具结果格式化为纯文本（AI 读纯文本，终端 `print()` 输出）。
3. **失败要诚实，不许编**：工具失败统一打 `[工具失败]` 标记回传给模型，系统提示词强制"数据必须来自工具结果，禁止编造；[数据缺失] 必须如实转述"。这是金融场景 Agent 的底线设计。

## 二、总体架构

```
┌──────────────────────────  进程边界（子进程化）  ──────────────────────────┐
│  start.py ──subprocess──> python -m src.chat（__main__.py）                │
│                            │ 剥离代理env + UTF-8 + 依赖缺失友好提示          │
│                            ▼                                              │
│  ┌── run_chat_repl (agent.py) ── REPL 循环 ──────────────────────────┐   │
│  │  ChatAgent                                                        │   │
│  │   ├─ _messages 对话历史（system + 滑动窗口，_trim_messages 配对保护）│   │
│  │   ├─ _run_conversation()  ◄── 核心循环（§三）                      │   │
│  │   │    ├─ _call_api_stream()  流式优先（增量打印+tool_calls聚合）    │   │
│  │   │    ├─ _call_api()         非流式回退（max_tokens 三级降级）      │   │
│  │   │    └─ _execute_tool()     工具分发 + 结果首尾保留截断            │   │
│  │   └─ finally: shutdown_engines()  资源释放                          │   │
│  └────────────────────────────────────────────────────────────────────┘   │
│        │ TOOL_REGISTRY（11 个工具）                                        │
│        ▼                                                                  │
│  tools.py 模块级引擎单例（init_engines 一次初始化）                         │
│   ├─ Orchestrator（7层分析链 + PlanGuard + 高位止盈 + fundamental_alert）   │
│   ├─ ScannerEngine（全市场扫描，THS板块/概念解析）                          │
│   ├─ PortfolioManager（portfolio.yaml 持仓读写）                           │
│   ├─ RAGService（FAISS + bge-small-zh + jieba，55+章策略知识）             │
│   └─ industry_data（产业链图谱 + akshare 商品/需求数据层）                  │
└───────────────────────────────────────────────────────────────────────────┘
```

**文件职责**（四层分离，各管一件事）：

| 文件 | 行数 | 职责 |
|------|------|------|
| `agent.py` | 661 | ChatAgent 类：AI 客户端、function calling 循环、流式/非流式调用、历史裁剪、回复后处理、REPL |
| `prompts.py` | 312 | `CHAT_SYSTEM_PROMPT`（角色/命令桥规范/行业分析规范/知识库指南/输出规则）+ `TOOL_DEFINITIONS`（11 个工具 schema） |
| `tools.py` | 857 | `init_engines/shutdown_engines` + 11 个工具函数（含 v0.8.8 命令桥：Tee捕获/input补丁/run_command/manage_portfolio）+ `TOOL_REGISTRY` 映射 |
| `formatter.py` | 238 | 结构化数据 → 纯文本（供 AI 读 + 终端打印） |
| `__main__.py` | 35 | 子进程入口 `python -m src.chat` |

## 三、核心循环：Function Calling 状态机（agent.py `_run_conversation`）

这是整个 Agent 的心脏。轮次语义经过三轮实战迭代，值得细看：

```
用户输入 ──> [循环] ────────────────────────────────────────────┐
  │                                                            │
  │  调用AI（每轮都带 tools，tool_choice="auto"）                │
  │    ├─ 流式优先：正文增量打印 + tool_calls 按 index 聚合       │
  │    └─ 失败回退非流式                                         │
  │                                                            │
  ├─ 无 tool_calls → 后处理（§四）→ 返回回复，结束               │
  ├─ tool_calls 被截断(finish_reason=length) → 放弃执行，提示重试 │
  └─ 有 tool_calls → 逐个执行：                                 │
       ├─ json 解析失败 → 空参执行（模型自己承担后果）            │
       ├─ 结果以 [工具失败] 开头 = 失败轮，不占轮次上限，          │
       │   连续全失败 ≥3 轮 → 跳出（防工具坏了无限重试）           │
       └─ 成功 → round_num += 1                                 │
                                                            │
  轮次上限：hard_cap = max_tool_rounds(默认5) × 2             │
  超限跳出 → 追加 nudge 消息"基于已获取信息直接回答" ───────────┘
  最终兜底调用【不带 tools】→ 模型只能输出文本，不能再调工具
```

**四个关键设计决策**（每个都对应一次真实事故）：

1. **每轮都带 tools**（而不是上限后摘掉）：早期实现达到轮次上限后不带 tools 再调一次，模型还想调工具时会把 `<tool_calls>` 伪 XML 当正文输出并停止——用户看到一段假 XML 而非回答。改为上限内每轮带 tools + 超限后用 nudge 消息要求直接作答。
2. **失败轮不占轮次上限**：一轮内所有工具都失败时（数据源挂了很常见），失败信息回传给模型让它自行重试换参数，不消耗轮次；但连续全失败达 `max_failed_rounds=3` 就停止，防止工具彻底坏了无限空转。成功与失败用结果前缀 `[工具失败]`（`TOOL_ERROR_MARK`）区分——一个字符串约定撑起整个失败语义。
3. **截断的 tool_calls 不执行**：`finish_reason=length` 时工具参数 JSON 多半残缺，`json.loads` 失败后空参执行会让模型"装失忆"。检测到截断直接放弃本轮执行，提示用户简化问题重试。
4. **超限后 nudge 而非硬停**：达到 hard_cap 后追加一条 user 消息"请基于以上已获取的信息直接给出完整回答"，再做最后一次**不带 tools** 的调用——既尊重轮次上限，又不浪费已获取的上下文。

## 四、回复后处理（`_reply_parts`）——模型输出的"安检"

LLM 输出不总是干净的，回复返回给用户前过三道检查：

1. **空 content 兜底**：思考型模型偶发 content 为空但 `reasoning_content` 有内容 → 用思考内容兜底；再空则给占位提示。
2. **伪 `<tool_calls>` 剥离**：模型在无 tools 可用时把工具调用当正文输出（正则剥离），并追加提示"本次回答不完整，可输入'继续'"。
3. **截断提示**：`finish_reason=length` 时明确告知用户"触及输出上限，可输入'继续'补全"。

流式路径下正文已实时打印，REPL 通过 `_last_reply_printed` 标志避免重复打印，只补打提示语。

## 五、流式实现（`_call_api_stream`）

- **正文增量**：`delta.content` 逐片 `print(end="", flush=True)`，首片前补换行、收尾补换行。
- **tool_calls 流式聚合**：工具调用参数是分片到达的，按 `index` 聚合 `{id, name, arguments[]}`，结束后 `"".join(args)` 还原完整 JSON，组装成与非流式**形状兼容**的 `SimpleNamespace` message——下游代码无感知。
- **reasoning_content**：思考型模型的思考增量单独收集（不打印，仅兜底用）。
- **降级**：创建或中途异常 → 回退非流式 `_call_api`（其内部还有 max_tokens 三级降级 32768→8192→4096）。

## 六、模型适配层（多供应商细节）

| 关注点 | DeepSeek-V4 | Kimi（k2.5/k2.6） |
|--------|-------------|--------------------|
| 思考模式 | 默认开启，需 `extra_body={"thinking":{"type":"disabled"}}` 关闭 | — |
| 输出上限参数名 | `max_tokens`（拉满 384000） | `max_completion_tokens`（max_tokens 已弃用） |
| temperature | 支持 | **不支持**（`_should_pass_temperature` 过滤） |

- max_tokens 显式拉满到模型上限（384K），避免服务端小默认值静默截断长回答；API 拒绝时按 32768→8192→4096 逐级降级重试（只对"输出超限"类错误降级，避免 context 超限误触发）。
- 客户端统一 `timeout=180, max_retries=2`（金融分析回答长，超时比常规 chat 宽）。

## 七、十一个工具与数据来源全景

| 工具 | 功能 | 底层引擎 | 数据来源 |
|------|------|---------|---------|
| `analyze_stock` | 单股深度分析（7 层链路） | Orchestrator.analyze | Baostock K线/财务 + 新浪实时快照 + akshare 公告/股东户数/融资余额 + AI 情绪（DeepSeek） |
| `scan_market` | 全市场规则扫描 | ScannerEngine.quick_scan | 新浪全市场快照（主）+ eFinance（备）+ THS 板块/概念成分股 + Baostock 60日趋势；v0.8.8 起结果同步写 session_state（#N/l all/ba 跨命令接续） |
| `search_stocks_by_sector` | 行业板块成分查询 | ScannerEngine.get_industry_list | THS 板块接口 |
| `get_portfolio` | 持仓查询 | PortfolioManager | `portfolio.yaml` |
| `get_news` | 个股+宏观新闻 | NewsClient | akshare 新闻接口 + 巨潮公告备用源 |
| `search_knowledge` | 策略知识检索 | RAGService | FAISS 向量库（`投资策略（持续更新）/` 55+章，bge-small-zh 嵌入） |
| `analyze_industry` | 产业链深度分析 | industry_data.build_industry_report | 产业链图谱（手写 YAML + AI 自举沉淀）+ akshare 期货/现货基差/仓单 + 乘联会/能源局/统计局月度数据 + THS 成分股（全行业通用引擎） |
| `get_main_business` | 个股主营构成 | industry_data.fetch_main_business | akshare 东财 F10（须带 SZ/SH 前缀） |
| `save_chain_graph` | 图谱自举（AI 把梳理的产业链结构沉淀为 YAML，下次复用） | industry_data.save_auto_chain | 写 `configs/industry_chains_auto.yaml`（schema 校验+长度上限） |
| `run_command` | **v0.8.8 命令桥**：执行 REPL 任意原生命令 | start.parse_input + start.run_cli | 与 REPL 完全同一套调度代码（详见 §七·五） |
| `manage_portfolio` | **v0.8.8 持仓修改**：建仓/清仓/字段改/计划/超配 | cli.main.manage_positions + PortfolioManager.update_position_fields | 读写 `portfolio.yaml`（原子写+.bak 备份） |

**工具层的可靠性约定**（每个工具都遵守）：
- 网络调用一律带超时：akshare 类 25-30s（daemon 线程硬超时，如 `_get_stock_data_with_timeout`），超时降级（如 analyze_stock 数据超时→退化为纯实时行情快照）。
- 所有异常在工具层捕获，返回 `[工具失败] + 原因`，绝不向上抛异常打断对话循环。
- 结果超长（>4000 字符）时**保留首尾、截断中间**——因为格式化器把决策理由/风险提示放在末尾，切尾巴会喂给模型残缺信息。

## 七·五、命令桥（v0.8.8）：run_command / manage_portfolio

> 需求：chat 连通 REPL 全部命令 + 安全修改 portfolio.yaml。经对抗审查（含子进程备选方案评估）后采用进程内桥接。

### 设计核心：单一真相源

`run_command(command, confirm)` 惰性 `import start`，复用 **start.parse_input + start.run_cli**——与 REPL 完全同一份调度代码。行为平价由构造保证：REPL 新增命令 chat 自动可用，不存在"chat 与 CLI 分歧"的对账问题（对比 §八 的三次历史事故）。逐命令写结构化工具的方案被否决：~25 个 mode 的语义（`ba` 的缓存检查+y/N、`l all` 的去重记账、`#N` 解析、规则名模糊归位）全部活在 parse_input+run_cli 里，重写=双份真相源必然漂移。**子进程执行方案**（`python -m src.cli.repl_exec` 管道捕获，EOF 自动取消确认）也被评估过：更简单更稳（logging/SystemExit/client 累积天然隔离），但每命令 2-4s 启动开销+RAG torch 冷加载 10s 级，体验差，故否决。

### 输出捕获：Tee 双写

`_TeeBuf(io.StringIO)` 构造时捕获真实 stdout，write 双写（实时回显终端 + 缓冲喂 AI）。Rich `Console.file` 每次渲染动态解析 sys.stdout（cli/main.py 的 console 未固定 file），redirect_stdout 后 Rich 表格与 print 全落缓冲；非 tty 自动无 ANSI。

### 交互确认映射：input 补丁 + confirm 硬门

REPL 命令里的 `input()` 交互（ba/l all 的 y/N、pos add 的 TradePlan 草稿采用）由 `_InputPatcher` 接管：提示含 `y/N`/`Y/n` → `confirm ? "y" : "n"`；其他提示（scan market 选股菜单）或**空提示**（Rich console.input 内部调无参 input）→ "q" 安全跳过；提示词+答案回显进 Tee（否则用户和 AI 都不知道问题出现过）；try/finally 恢复 builtins.input（管道模式泄漏会废掉 REPL 主循环）。

**confirm 硬门**（不靠提示词软约束，模型不听话也拦得住）：`l all`/`ba`/`bz scan`/`scan market deep`/`pos plan --update|all`/`bz --refresh` 与 `manage_portfolio` 的 add/remove/update/overweight，不带 confirm=true 直接返回 `[工具失败]` 拒绝。AI 必须先在对话中征得用户明确同意（系统提示词纪律），REPL 的 y/N 语义完整映射到 chat 对话。

### 块列表（chat 中不可用）

`chat`（防递归子进程）、`q/quit/exit/h/help`（会话级）、`noai/debug`（REPL 进程级状态跨不到 chat 子进程）、`bz --manual`/`bz` 空参（交互式打分循环）、`pos add/rm/overweight`（重定向到 manage_portfolio 结构化工具）、`chains rm`（parse_input 解析阶段就执行删除，无确认破坏性，必须在 parse 前拦截）。

### 已知局限（诚实清单）

- **logging 捕不到**：`logging.basicConfig` 的 StreamHandler 在进程启动时绑定真实 stderr，redirect_stderr 换不掉已绑定句柄——命令期间的降级告警靠 plain_errors 人话汇总（drain_new+render_summary，照抄 start.py 主循环收尾）追加喂给 AI；未命中映射表的原始 warning 仅终端用户可见。
- tqdm 进度条写真实 stderr，用户可见、AI 不可见（纯视觉噪音，无信息损失）。
- `manage_portfolio(plan)` 单股查看/生成与 REPL 平价不设 confirm 门（批量 update 走 run_command 有门）。
- 工具结果仍受 4000 字符首尾截断，长命令（回测/bz scan）中段可能被切。

### 配套一致性修复

- **持仓无条件 reload**：run_command/manage_portfolio 结束后必重读 `_portfolio_manager`——l/la/l all 回写策略状态、pos plan 写计划都发生在新建 PM 实例上，chat 层若持陈旧快照，下次 analyze_stock 的 update_from_strategy_decision 会把旧快照整体写回、**静默回滚刚做的修改**（数据丢失向量，不能按"是否写操作"枚举）。
- **RAG 单例对齐**：init_engines 把 chat 建的 `_rag_service` 注册为 `rag.service._rag_service_singleton`——CLI 的 TradePlan 路径（cli/main.py `_try_attach_trade_plan`/`_generate_or_update_plan`）调 get_rag_service() 懒加载，不对齐会在 chat 进程内二次加载 torch+FAISS（内存翻倍）。shutdown_engines 对应清空。
- **scan_market 同步写 session_state**：chat 内扫描结果存 `~/.muyun/last_scan.json`（与 CLI 同一状态文件，跨进程可见），`#N`/`l all`/`ba`/`pos add #N` 全链路接续。
- **SystemExit 接住**：CLI 内部数据失败 sys.exit(1)（SystemExit 是 BaseException，agent 层 except Exception 抓不住），漏接会杀死整个 chat 会话。

## 八、与 CLI 的行为一致性（血泪史）

chat 复用 CLI 引擎，但"复用"不等于"自动一致"——历史上至少三次因漏传参数导致 chat 与 CLI 对同一股票给出不同结论，每次都以"审计 CLI 同名路径 → 补齐参数"收场：

| 事故 | 根因 | 修复 |
|------|------|------|
| chat 分析持仓股永不产生买卖点 | Orchestrator 漏传 `entry_exit_config/pyramid_config` → 买卖点计算器为 None | init_engines 补齐 |
| chat 对持仓股的决策与 CLI 不同 | `analyze` 漏传 `has_position/entry_price/high_since_entry/trade_plan` → PlanGuard/止损/高位止盈全失效 | analyze_stock 补齐 7 项 |
| 持仓策略状态随时间分歧 | chat 只读不写 → inertia/cooldown 冻结 | 分析后回写 `update_from_strategy_decision`（仅持仓股，防创建虚假记录） |

**经验**：多入口复用同一引擎时，参数传递要有一个"对账清单"，每次改引擎签名必须同步检查所有入口。

## 九、资源生命周期与子进程化

**问题**：RAG 的 torch 嵌入模型 + FAISS 索引（~440MB 含库导入开销）随模块级单例驻留；Python 进程内无法卸载已导入的 C 扩展模块。

**两阶段解决**：
1. `shutdown_engines()`（进程内清理）：关 AI 客户端 httpx 连接池 → baostock 登出（仅当登录过）→ 置空 4 个引擎单例 → `gc.collect()`。挂在 REPL 的 `finally`（q/EOF/Ctrl+C/异常全覆盖）。实测回收 ~55MB（弱引用验证 torch 模型确被 GC）。
2. **子进程化**（治本）：start.py 通过 `subprocess` 调 `python -m src.chat`，主进程永不 import chat/RAG/torch；chat 退出即由 OS 整体回收全部 ~440MB。附带收益：主进程启动更快、chat 依赖缺失不再拖垮主程序（`__main__.py` 里友好提示 `uv sync`）。

**新机器初始化**：chat 依赖 openai/jieba/faiss-cpu/sentence-transformers + RAG 模型 + API key，`__main__.py` 的 ImportError 兜底会列出缺什么。

## 十、对话历史管理

- 滑动窗口：`system + 最近 max_history(20) 条`。
- **配对完整性保护**（`_trim_messages`）：裁剪不能切断 `assistant(tool_calls) → tool` 消息对——窗口开头若出现孤立 tool 消息（其 assistant 已被裁掉），API 直接 400。裁剪后丢弃开头连续的孤立 tool 消息。
- `reset` 指令重置历史（保留 system）。

## 十一、配置项（configs/settings.yaml `chat:` 段）

| 键 | 默认 | 说明 |
|----|------|------|
| `max_history_messages` | 20 | 对话窗口条数 |
| `max_tool_rounds` | 5 | 工具轮次（硬上限 ×2） |
| `max_result_length` | 4000 | 单工具结果喂给模型的最大字符数 |
| `max_tokens` | 384000 | 输出上限（V4 拉满） |
| `stream` | true | 流式输出（失败自动回退） |
| `persist` | 代码缺省 false（当前 settings.yaml 设为 true） | 对话落盘到 `分析报告/chat/YYYY-MM-DD.md` |

## 十二、测试与已知局限

**测试**（tests/chat/，纯 mock 无网络）：streaming（流式聚合/回退）、tool_failure（失败语义/轮次）、reply_finalize（伪 tool_calls/截断提示）、shutdown（资源释放/幂等）、history_trim（配对保护）、formatter。

**已知局限（诚实清单）**：
1. 工具结果 4000 字符截断仍是信息损失（保留首尾是缓解不是消除）。
2. `analyze_stock` 数据超时降级后只剩实时快照，深度分析退化为"有价无指标"。
3. 多工具并发不存在的——工具串行执行，一次行业深度分析（图谱+商品+需求+主营抽样）可能耗时 1-2 分钟。
4. 对话历史窗口 20 条，超长多轮推理的早期上下文会被裁掉（有配对保护但无摘要压缩）。
5. 模型行为依赖提示词约束（禁止编数据），无程序级事实校验——提示词注入风险与所有 LLM Agent 同在。

## 十三、演进时间线（供学习参考）

| 版本 | 里程碑 |
|------|--------|
| v0.8.0 Phase 5 | ChatAgent 雏形：5 工具 + function calling 循环 |
| v0.8.1 | +RAG 知识检索工具；H2 超时守护 |
| 2026-07 审查 | H1 参数补齐（与 CLI 对账）/ M1 截断 tool_calls 不执行 / 伪 tool_calls 剥离 / DeepSeek-V4 迁移 |
| ISS-058~060 (08-15) | 伪 tool_calls 无回答根因修复（轮次语义重设计）/ 工具失败重试语义+流式进度 / shutdown_engines |
| ISS-061 (08-15~17) | analyze_industry 产业链数据层 + 图谱自举 save_chain_graph（Agent 自我沉淀知识的自举设计）+ max_rounds 3→5 + 对话落盘 |
| 08-16~17 | token 级流式输出（stream 优先+回退）/ chat 子进程化（-440MB）/ jieba 刷屏修复 |

> **给 Agent 开发者的三个可复用要点**：① 失败语义用"结果前缀标记 + 失败轮不占配额 + 连续失败熔断"三层设计，比简单的 try/except 健壮得多；② 多入口复用引擎时，参数对账清单是防行为分歧的唯一手段；③ 内存大头（torch 模型）在 Python 进程内无法卸载，子进程化是唯一彻底方案。
