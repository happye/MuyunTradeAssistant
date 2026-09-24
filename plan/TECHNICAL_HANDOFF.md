# 技术交接与可认领任务

日期：2026-09-24。**仅方案，未实施。** 与 [里程碑](README.md) 配套使用。

## 1. 接手阅读顺序

| 资产 | 用途与注意事项 |
|---|---|
| `AGENTS.md`、`skills/muyun-dev-discipline/SKILL.md` | 工程约束、运行链路、回测与缓存红线 |
| `CLAUDE.md`、`CODEX.md` | 工具协作偏好；其中“测试不是 pytest”与仓库事实不符，不能照抄 |
| `.learnings/LEARNINGS.md`、`ERRORS.md` | 特别看 2026-09-06 模型加载事故、09-22/23 写盘副作用与测试隔离事故 |
| `ISSUES.md` | 历史裁决；顶部待办并非全部最新，需要查对应实现 |
| `docs/2026-09-05_代码重构交接文档.md`、`2026-09-05_瘦身重构会话方案.md` | 旧重构边界与实际完成记录；不要把已删除的兼容层重新作为待办 |
| `docs/2026-09-11_测试体系架构梳理报告.md` | 测试组织问题；指标数学脚本已由 `tests/core/test_indicator_math_script.py` 接回回归 |
| `.claude/plans/` | 体验重构、阶段转换、TUI、chat 恢复的历史意图，需逐项查当前实现 |
| `C:/Users/Crux/.claude/projects/G--Tools---------/memory/` | 已读取 MEMORY 索引与条目；保留用户偏好、已否决方向和模型/超时事故线索 |
| `.workbuddy/memory/MEMORY.md` | 已读取；存在旧版本、已删除 API、旧角色分工等过期信息，只作考古 |

不可照搬的旧结论包括：`tests/ui` 不存在（当前两个测试文件实际存在）；CLI 没接 RAG（已见 `_cli_rag` 接线）；`load_pyramid_config` 还要删除（历史重构已完成）；指标数学验证默认不跑（已有 wrapper）。

这次建立了主要模块与重点风险图谱，**没有完成 36,455 行源码的逐行审计、所有历史文档的事实复核、真实数据源验收或回测收益比较**。

## 2. 原代码测试基线

使用项目 `.venv/Scripts/python.exe`。在独立子进程中将 `HOME`、`USERPROFILE` 指向一次性临时目录后运行：

```text
python -m pytest tests/ -q --ignore=tests/artifacts --ignore=tests/rag_eval --ignore=tests/data_sources/test_all_api.py -k "not test_check_all_sources_structure"
```

结果：**741 passed，3 failed，2 skipped，1 deselected；114.02 秒**。

本机日志：`tests/artifacts/iteration_20260924/baseline.txt`（gitignored，仅本机证据；关键结果已记录于此）。命令自身没有隔离用户目录的功能，接手者不能只复制命令就声称数据隔离。此基线排除了明确的外源体检，未证明所有底层或子进程均无网络。

| 失败测试 | 已核实原因 / 后续做法 |
|---|---|
| `test_vol_mom_falls_back_when_baseline_missing` | 测试固定序列截至 2026-09-10、baseline=09-11，但 `metrics/market_metrics.py::_metric_vol_mom` 的 7 天陈旧判断直接调用 `datetime.now()`。09-24 执行自然得到 MISSING。先固定被测模块的时钟，保留“超 7 天 MISSING”反例；是否把函数改为相对 baseline 判定，需要另行明确 live 与历史语义。 |
| `test_get_fear_report_assembles_and_snapshots` | 测试 patch `history.recent_trade_date`，实际 `fear_index/__init__.py` 已用 `from ... import recent_trade_date` 绑定本地名字，所以写出的快照是 09-24。patch 实际查找名字的模块，断言保持固定预期日期。 |
| `test_planguard_take_profit_passthrough` | plan 固定 opened_at=2026-06-19、max_hold_days=90，evaluate 未传 today；到 09-24 已过期，优先触发 time_stop。用已有 `today` 参数固定场景，同时保留到期日恰好 90 天的边界测试，不能为测试绿灯放宽生产止损。 |

两项 skip 是默认不启用的真实 AI E2E。未来“离线全绿”与“外源可用”应分开报告，不能混成一个质量数字。

## 3. M1：先建立可靠验证入口

**负责文件建议**：`pytest.ini`、`tests/conftest.py`、上述三个时间相关测试、`tests/README.md`；不改策略实现。

实施顺序：

1. 固定发现目录与排除目录，注册 external/ai 标记；保持脚本直跑入口。
2. `test_all_api.py` 在 import 时清代理环境、调整 stdout、修复证书，默认须在收集前排除。`-m 'not external'` 在收集后才生效，单独使用不能防副作用。
3. 保留 `tests/data_sources` 中纯 mock 测试；`test_source_check.py::test_check_all_sources_structure` 文案自相矛盾，实际调用 `check_all_sources`，应明确列为外源。
4. 在产品模块 import 计算路径之前完成测试用户目录隔离，确保子进程继承。每用例的状态隔离必须遵守已有测试注入点。
5. 禁网要考虑 Python socket、curl_cffi 原生网络及子进程：仅 monkeypatch socket 不能声称全面断网。可先明确测试分组，再把未 mock 的网络路径逐一查清。
6. 用三个已存在失败证明日期修复有效，不改变业务断言；跑完整离线测试记录真实构成。

**试作已撤回，但暴露的约束要保留**：一次尝试在根 autouse fixture 中强制重设 session_state 全部路径，覆盖了 `test_chat_command_bridge.py` 的模块级临时路径，导致 `test_manage_portfolio_hash_n_resolve` 失败。不能把“增加通用隔离 fixture”等同于正确隔离。先统一 fixture 层级或采用独立子进程目录方案，再逐一验证已有 monkeypatch。试作还出现 watch 展示断言失败，未定性，不能记作原产品回归。

验收：用户 home 的相关状态文件前后摘要不变；纯 mock 数据源用例仍被收集；真实接口必须显式启用；测试跨日期运行仍稳定。

## 4. M2：会话快照及事件流容错

**负责文件建议**：`src/cli/session_state.py`、相应 session/review/watch 测试、`plain_errors.py`、报错速查手册。

已核实的调用关系：

- `save_last_scan` 接收 REPL 扫描、CLI 扫描、chat 扫描结果，写 last_scan 并追加 scan_history。
- `resolve_index` / `last_scan_count` / `start.run_cli` 读取 last_scan；`l all` 用 deep_analyzed 去重。
- scan_review 消费 history；get_watch_active 重放 watchlist，供 watch 命令及分析后自动入池使用。

方案：

- 快照采用同目录唯一临时文件 → 写完并关闭 → `os.replace`。先序列化后触碰磁盘，失败清理本次临时文件并保留旧快照；不要复用固定 `.tmp` 文件名造成两个写者互抢。
- 输入与读取边界检查：JSON 根类型、items 是否列表、item 是否对象、code 是否可用、timestamp 类型及格式。last_scan 不应过滤坏条目后重排 `#N`，否则同一序号指向别的股票；应拒绝整个坏快照并说明重扫。历史和观察池可逐行隔离，但要说明被跳过的行数，不能假装完整。
- deep_analyzed 拒绝非 dict 根对象，防 `.get` 抛 AttributeError。时间戳排序不能直接混排 None/数字/字符串。
- JSONL 按物理行读取，单行无效 UTF-8 或截断不拖垮其余有效行；保留原文件供人工恢复。继续纯追加，不做按年龄 prune。
- 写入返回值应如实表达“快照已保存、历史追加失败”的部分成功，先梳理调用方再设计返回契约，不能只加日志就当事务完成。

关键测试：替换失败/序列化失败旧文件字节不变；空文件、`null`、数组根、items 类型错误、混合 timestamp、单行坏编码、截断尾行；#N 不错位；旧历史不丢；add→remove→add 的入池锚点语义不变。

**边界**：原子替换只保证读者看到完整文件，不保证多进程读改写合并；watch 事件流的重复事件与文件锁另作评估，不能泛称“已解决并发”。

## 5. M3：扫描报告保留与导入幂等

**负责文件建议**：`session_state.persist_scan_report`、`main.scan_review_import`、对应测试。可能与 M4 编辑同文件，必须串行合并。

已核实：报告文件名目前只有分钟精度，同主题再次写入直接 `write_text` 覆盖。导入用 `{timestamp[:16]: source}` 的 dict 表示已知记录，同一分钟有多个 source 会互相覆盖，而且本轮新导入后没有把键加入集合。

方案：

1. 新报告增加秒/微秒或独立唯一标识，使用独占创建防同名覆盖；正文保存精确扫描时间，格式带可辨识版本，避免误认旧文件。
2. 新格式按精确时间+来源去重；旧报告保留原分钟语义。已落盘历史仅有秒精度时，新格式如何关联原扫描记录，须先确定共同 scan_id 或一致 timestamp 传递方式。
3. 使用复合键集合表示已存在记录；成功追加才更新集合，保证同一批次重复文件不重入、追加失败可重试。
4. 解析旧格式兼容测试不得用新报告生成器伪装旧文件，应提供固定旧格式文本 fixture，防两端一起变而“兼容测试”虚假通过。

验收：固定时钟连续同主题扫描两次，两份内容均保留；旧格式导入不变；同分钟不同主题全部保留；重复执行与同批重复文件不增加行；损坏文件和写盘失败各自计数可见。

## 6. M4 / M5 / M6：后续重构顺序

**M4 CLI 拆分**：先把 `_sparkline`、`_offset_curve`、`_review_path` 中纯计算与 IO 拆开，建立 review 数据结构；随后迁移 review 命令与展示。`main.py` 暂留旧符号的兼容出口，避免 tests 和 chat 同时迁移。保留 Rich 的动态 stdout 行为，chat 的 Tee 捕获依赖它。不能只为减少文件行数增加一层通用服务框架。

**M5 持仓写入**：`PortfolioManager._save` 发现 mtime 变化仍覆盖原文件；add/remove/update/attach_plan 等调用方的返回传播需先画全。内容指纹比单 mtime 更可靠，但 check→replace 仍有竞争窗口，跨进程安全需要共同锁或真正的比较交换存储。方案必须同时覆盖内存回滚、`.bak`、TradePlan 保留、CLI/chat/Web/TUI 的失败提示。设计测试先模拟两个 manager 读同版本、一个先保存、另一个后保存，证明不会覆盖较新的用户操作。

**M6 安装与诊断**：仓库只有 requirements.txt，文档却推荐 uv sync。先选一个安装事实源：补项目元数据与锁文件，或统一为 requirements 安装命令；不盲目升级依赖。诊断命令默认只读、零 AI，输出解释器/依赖/配置存在性/索引及缓存状态，不回显密钥。Agent 入口的历史版本叙事迁入归档，保留约束和指针；全局 Claude 配置与个人记忆不在本计划默认编辑范围内。

所有策略收益优化、评分新维度和行情源替换均在上述工程基线稳定后另行设计，要求 point-in-time 数据可得性和明确 A/B 标准。

## 7. 本次交付边界与执行教训

- 只保留 `plan/` 文档；没有业务源码、测试或依赖配置改动，没有提交或推送。
- 已有用户修改及未跟踪资产原样保留。测试运行产物在 gitignored artifacts 目录，不能把它们当成可跨机器读取的交接材料。
- 本次先写代码后收到范围纠正，已全部撤回。后续接手者以用户最新任务为准：本轮只需计划，不因“允许重构”的背景描述自行开始实施。
- 工具中出现的路径猜测和 PowerShell 字符串转义失败未涉及产品缺陷；后续先 `rg --files` 确认路径，多行 Python 用 PowerShell 单引号 here-string，避免把工具失败记成业务问题。
