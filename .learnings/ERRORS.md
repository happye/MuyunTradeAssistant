# Errors

## [ERR-20261010-ARCH16] Q审查先猜测路径导致无效搜索

**Logged**: 2026-10-10 | **Status**: resolved | **Area**: review tooling

读取时间/行情助手时猜测quote_context.py、batch_quotes.py及PowerShell路径通配，rg报路径不存在。已先用rg --files发现现有source_time.py，按真实位置复核；无产品改动、无验收结论基于失败搜索。延续既有教训：文件枚举先于猜测路径。

## [ERR-20261010-ARCH14] 恢复入口补丁使用截断的标题上下文

**Logged**: 2026-10-10 | **Status**: resolved | **Area**: review tooling

补丁锚点须来自未截断的原文；链接检查须在目标产物生成后执行。旧专项结果数字与临时索引生成过程已过期，不保留在记忆中。

## [ERR-20261001-ARCH05] R12 probe fixture missing required model fields

**Logged**: 2026-10-01 | **Status**: resolved | **Area**: review tooling

B2首次构造StockData漏stock_name/volume，Pydantic拒绝；没有产品失败结论。补完整fixture后独立重跑确认probe_errors=0。避免用多命令shell末命令成功掩盖先前探针非零；最终收口工具逐项验证JSON中的probe_error。

## [ERR-20261001-ARCH06] Native FAISS write bypassed Python review guard

**Logged**: 2026-10-01 | **Status**: resolved | **Area**: review isolation

初次281目标测试虽绿，14:43:14生成仓库knowledge/index/index.faiss.tmp（16,740,045字节），后续replace被拒；portfolio/bak哈希不变。文件hash、清理条件及限制见iteration6/R12_ACCEPTANCE.md。已对可选RAG/原生写边界和知识库文件前后检查补防护，保留首次日志，重跑目标集。没有事前知识库全量hash，不能把Git无变化扩大为首次全目录无写入。

Command failures and integration errors.

---

## [ERR-20260718-001] uv_run_pytest

**Logged**: 2026-07-18T00:15:00+08:00
**Priority**: medium
**Status**: pending
**Area**: tests

### Summary
`uv run pytest` 走系统 python（.venv 无 pytest），导致 import 第三方包（textual）失败。要用 `uv run python -m pytest` 走 .venv python。

### Error
```
ModuleNotFoundError: No module named 'textual'
```

### Context
- .venv 装了 textual（uv pip install textual），但没装 pytest（历史测试用系统 pytest）
- `uv run pytest` 找到系统 PATH 的 pytest.exe（系统 python，无 textual）
- `uv run python -c "import textual"` 成功（用 .venv python）
- 已补装 pytest==9.1.1 + pytest-asyncio==1.4.0 到 .venv（requirements.txt 已加）

### Suggested Fix
测试统一用 `uv run python -m pytest tests/...`（强制 .venv python + pytest 模块），不用 `uv run pytest`。

### Metadata
- Reproducible: yes
- Related Files: tests/test_tui.py, requirements.txt
- Tags: uv, pytest, venv

---

## [ERR-20260515-001] run_in_terminal

**Logged**: 2026-05-15T15:40:00+08:00
**Priority**: medium
**Status**: pending
**Area**: tests

### Summary
VS Code 终端内已有交互式 CLI 占用时，run_in_terminal 的一次性诊断命令会被当前程序吞掉并误解析为业务输入。

### Error
```text
所有股票深度分析均失败
输入代码深度分析(如: 600546 002192) 或 all(全选) 或 q(跳过)
> import requests, json
```

### Context
- Attempted to run a one-shot Python probe to inspect the Sina market API response.
- The persistent shell had an interactive scanner/deep-analysis session still attached.
- Probe text was consumed as CLI input and triggered unrelated Baostock and scanner errors.

### Suggested Fix
在存在交互式终端残留时，优先使用代码静态检查、单文件诊断，或显式切换到干净终端再做运行验证。

### Metadata
- Reproducible: yes
- Related Files: src/scanner/market_cache.py

---

## [ERR-20260519-001] git commit

**Logged**: 2026-05-19T00:00:00+08:00
**Priority**: low
**Status**: pending
**Area**: infra

### Summary
PowerShell 下多段中文 commit message 首次执行时引号拼接失败，触发 pathspec 错误。

### Error
```text
error: pathspec '看不到买卖点' did not match any file(s) known to git
```

### Context
- Attempted to run git commit with multiple -m arguments containing Chinese punctuation via terminal wrapper.
- First invocation had malformed quoting.
- Retried with corrected quoting and commit succeeded.

### Suggested Fix
PowerShell 中提交复杂多段消息时，优先使用单引号包裹每个 -m 内容；失败后立即重试并保留最小可复现错误。

### Metadata
- Reproducible: yes
- Related Files: .learnings/ERRORS.md
- See Also: ERR-20260515-001

---

## [ERR-20260521-001] main.py syntax regression

**Logged**: 2026-05-21T00:00:00+08:00
**Priority**: high
**Status**: pending
**Area**: tests

### Summary
The sell-point display edit accidentally truncated the `pm.suggest_update(...)` call in `src/cli/main.py`, leaving an unmatched closing parenthesis.

### Error
```text
SyntaxError in main.py at line 565: unmatched ')'.
```

### Context
- Attempted to revise portfolio sell-point text to show清仓/减仓 meaning.
- The surrounding `pm.suggest_update(...)` block was partially overwritten.
- Syntax check on `src/cli/main.py` failed immediately after the edit.

### Suggested Fix
Restore the full `pm.suggest_update(pos.stock_code, stock_data.stock_name, strategy_decision, stock_data, console)` call and rerun syntax validation.

### Metadata
- Reproducible: yes
- Related Files: src/cli/main.py

---

## [ERR-20260525-001] run_in_terminal powershell exe invocation

**Logged**: 2026-05-25T00:00:00+08:00
**Priority**: medium
**Status**: pending
**Area**: tests

### Summary
通过 `run_in_terminal` 在 PowerShell 中直接执行绝对路径的 `python.exe` 时，未加调用运算符或未加引号会触发命令解析错误。

### Error
```text
The term 'c:\Project\Muyun\.venv\Scripts\python.exe' is not recognized as the name of a cmdlet...
The ampersand (&) character is not allowed...
```

### Context
- Attempted to run focused Python validation commands against the project virtualenv.
- Plain absolute executable path failed in PowerShell parsing under the terminal wrapper.
- Using `& "c:\Project\Muyun\.venv\Scripts\python.exe" ...` succeeded and produced the expected validation output.

### Suggested Fix
在 PowerShell 的 `run_in_terminal` 命令里，执行绝对路径 exe 时统一使用带引号的调用形式：`& "...\python.exe" script_or_args`。

### Metadata
- Reproducible: yes
- Related Files: .learnings/ERRORS.md
- See Also: ERR-20260515-001

---

## [ERR-20260526-001] rag evaluator offline model init

**Logged**: 2026-05-26T00:00:00+08:00
**Priority**: medium
**Status**: pending
**Area**: tests

### Summary
运行 RAG 评估脚本时，脚本先尝试初始化在线嵌入模型，离线环境会在指标计算前直接失败。

### Error
```text
RAG服务初始化失败: 嵌入模型加载失败: We couldn't connect to 'https://hf-mirror.com' to load the files, and couldn't find them in the cached files.
错误: RAG初始化失败
```

### Context
- Attempted to validate updated labels with `.\.venv\Scripts\python.exe tests\rag_eval\evaluator.py`.
- The script tried to initialize the full RAG service before reading existing retrieval results and labels.
- Current environment had no cached model files and could not reach the Hugging Face mirror.

### Suggested Fix
给评估脚本增加离线模式或纯指标模式：在已有检索结果和标签文件存在时，跳过 RAG 初始化，仅执行指标计算与报告生成。

### Metadata
- Reproducible: yes
- Related Files: tests/rag_eval/evaluator.py
- See Also: ERR-20260525-001

---

## [ERR-20260724-001] powershell_nested_command

**Logged**: 2026-07-24T00:00:00+08:00
**Priority**: low
**Status**: pending
**Area**: infra

### Summary
PowerShell parsed an embedded Python `-c` command instead of passing it through to `cmd /c` because nested quoting was escaped for the wrong shell.

### Error
```text
The 'from' keyword is not supported in this version of the language.
```

### Context
- A validation command used nested PowerShell, `cmd`, and Python quoting.
- The Python source was interpreted by PowerShell before `cmd` received it.

### Suggested Fix
Use separate short commands or a temporary script when validating Python expressions from PowerShell; avoid nested escaped quotes.

### Metadata
- Reproducible: yes
- Related Files: src/rag/embedding.py
- Tags: powershell, quoting, validation

---
## [ERR-20260830-001] git stash 中断导致 .git 引用层损坏

**Logged**: 2026-08-30T17:05:00+08:00
**Severity**: critical
**Status**: resolved

### Symptom
`git stash push` 执行中被中断（SIGTERM）后：`git status` → `fatal: bad object HEAD`；
`git fsck` → 全部 ref `invalid sha1 pointer`；`git cat-file -t <近期commit>` → `could not get object info`。

### Root Cause
- stash 的对象写入/引用更新非原子，进程中断留下半写状态；
- 2026-05 之后的 loose commit 对象损坏丢失（实测 650 对象仅 7 个 commit 可解析：
  2026-05-12 前历史 + 1 个 2026-07-16 WIP stash 快照 f27577f）。

### Rescue（无损修复，零文件删除）
1. 工作区文件核实完好（grep/测试逐项验证）
2. 旧 .git 整体归档 `.git.corrupted_20260830/`（另留 /tmp/d04_rescue/git_backup_dotgit）
3. `git init -b main` + 配回 origin + 全工作区快照 commit `0e9471c`（461 文件）
4. `git fsck --full` 零错误
5. 远端历史嫁接待用户凭据：`git fetch origin && git reset --soft origin/main && git commit && git push`

### Prevention
- agent 长任务链内禁裸跑 stash/checkout/reset/rebase；A/B 对照用文件级原地切换
  （备份→改→跑→还原→md5 校验，见 LRN-20260830-001）
- 仓库损坏先跑 `git cat-file --batch-all-objects --batch-check` 评估对象库存活——refs 坏 ≠ 对象丢，
  49 个 commit 对象在但仅 7 个可解析，说明逐个 parse 验证才能给出真实损失面

### Metadata
- Reproducible: yes（中断 stash 即可复现）
- Related Files: .git
- Tags: git, stash, corruption, rescue

---

## [ERR-20260904-001] bz scan 启动期误报 8 条「未知操作符 starts_with/contains」

**Logged**: 2026-09-04T00:30:00+08:00
**Severity**: low（纯告警误报，运行时行为正确）
**Status**: resolved

### Symptom
`bz scan shrink_pullback` 启动期连出 8 条 WARNING：
```
规则'global_exclude'过滤器校验失败: 过滤器#1: 未知操作符 'starts_with'，可用操作符: ['between', 'eq', ...]
```
（4 条 starts_with + 1 条 contains，×2 轮：`run_benzong_scan` 先建一个 ScannerEngine 解析规则名再建一个真扫描。）

### Root Cause
ISS-078（v0.8.8.2）给 `_load_rules` 接的加载期校验，对**所有 section** 用 `validate_filters`（普通 filters 的 `VALID_OPS`），而 `global_exclude` 段的执行器是 `apply_global_exclude`——合法支持 starts_with/contains/is_nan。校验器操作符集与执行器不匹配 → 合法配置误报；告警文案「该规则筛选变宽松」是假的（运行时排除一直正常）。

### Fix
`ScannerFilter.validate_excludes`（EXCLUDE_VALID_OPS 专属操作符集）校验排除段；`validate_filters` 只管规则 filters 段。回归测试 `tests/core/test_scan_rules_validation.py` 4 项：真实 YAML 零误报 + 真拼错操作符（规则段和排除段各一处）仍告警。v0.8.8.3。

### Prevention
校验器与执行器成对同步（见 LRN-20260904-001）；配置校验的回归测试必须用线上真实配置文件做断言。

### Metadata
- Reproducible: yes（v0.8.8.2 上跑任意 bz scan / --scan 即可复现）
- Related Files: src/scanner/scanner_engine.py, src/scanner/scanner_filter.py, src/scanner/scan_rules.yaml
- Tags: scanner, yaml-validation, false-alarm

---

## [ERR-20260905-001] expect 命令财报披露 Length mismatch 英文告警

**Date**: 2026-09-05
**Symptom**: `expect 60` 输出 `data_provider.calendar.disclosure 失败: ValueError:
Length mismatch: Expected axis has 0 elements, new values have 10 elements`，
每次执行都重复出现（不缓存），英文原文未命中人话映射直接透传吓用户。

### Root Cause
巨潮预约披露接口对**尚未发布预约表的报告期**返回空列表（`prbookinfos: []`），
akshare `stock_report_disclosure`（stock_yjyg_cninfo.py）对空数据直接
`temp_df.columns=[10个列名]` → 0 列 DataFrame 赋 10 列名必崩。触发窗口=每年
「预约表未发布的强制披露期」（实测 2026-09 的 2026三季；5-6 月半年报表同理）。
次要问题：`get_stock_disclosure` 的 `df.empty → return []` 早退绕过函数尾部的
缓存写入——空结果从不缓存，每次 expect 重复打接口。

### Fix
`calendar_client._fetch` 仅对 Length mismatch 签名 catch 转 `pd.DataFrame()`
（"该期间无预约表"是合法业务空态非故障；其余异常原样上抛 `_safe_call` 告警，
防 fail-open 吞真故障）+ `empty/None` 分离：空态入缓存、真故障（None）不缓存
保持重试语义。v0.8.8.4。回归 `tests/core/test_calendar_empty_period.py`：
空态三断言（[]/无 WARNING/二次命中缓存）+ 真故障守卫（超时仍告警）。

### Prevention
上游 akshare 对"合法业务空态"常以崩溃表达（0 列赋列名是惯犯模式）——对接
akshare 的包装层要把"接口返回空"翻译成业务空态而非告警；翻译必须**按异常签名
收窄**并保留真故障告警路径，不许裸 except。缓存语义要区分"空结果"（可缓存）
与"失败"（不缓存）。

### Metadata
- Reproducible: yes（v0.8.8.3 上跑 `expect 60`，9 月-三季表未发布窗口内必现）
- Related Files: src/data/calendar_client.py, .venv/.../akshare/stock_feature/stock_yjyg_cninfo.py
- Tags: akshare-upstream, empty-state, cache-semantics, fail-open-guard

---

## [ERR-20260906-001] 重排器默认开启致内存暴涨、用户机器一天重启三次（RAG 批事故）

**Logged**: 2026-09-06T21:30:00+08:00
**Severity**: high（用户机器不可用级别：稳定复现硬件级重启 3 次）
**Status**: resolved

### Symptom
用户跑 start.py / 分析 / chat 即触发 Windows 硬件级重启，稳定复现三次；机器
当天上午还发生三次蓝屏（Windows 记录故障模块 rt25cx21x64.sys，Realtek 2.5G
网卡驱动）。事故复盘（docs/2026-09-06_RAG变更对比与重启事故复盘.html）定论：
RAG 批把 reranker.enabled 写成 true（默认开启），每次启动自动加载
bge-reranker-base（1.04GB 权重），进程内存 1.2GB→3.5-4GB，叠加当时后台多个
验证进程各加载模型 → 内存耗尽触发硬件保护重启。

### Root Cause
两个错误叠加：① 会加载重型模型的开关默认值写成 enabled=true，且开启门槛
（内存预算）没写进配置注释；② 同一台机器上连续后台跑多个真实模型加载的
验证进程，未与用户会话错峰。

### Fix
reranker.enabled 回滚 false（代码保留，A/B 实测重排负收益 ON 0.433/0.232/0.286
vs OFF 0.633/0.553/0.568，永不该默认开）；残留 Python 进程清零。后续对抗审查
（本会话）进一步封死同机制的另一入口：embedding.model 动态解析本地 HF 快照、
命中即零联网加载，断网时"hub→失败→多源重试风暴"（蓝屏的另一嫌疑）路径不进入。

### Prevention
- 会加载模型的开关，默认值必须是关；开启门槛（空闲内存 ≥4GB）写进配置注释
- 重型模型验证不得与用户共用机器时段串行叠加
- 网络重试循环必须可被"本地命中"短路（快照解析模式，见 LRN-20260906-001）

### Metadata
- Reproducible: yes（reranker.enabled=true + 空闲内存 <4GB 跑 start.py 即复现）
- Related Files: configs/settings.yaml, src/rag/reranker.py, src/rag/embedding.py
- Tags: memory, model-loading, default-off, blue-screen, incident

---

## [ERR-20260911-001] push_github.py FETCH FAIL: TLS unexpected eof（瞬时，重试即过）

**Logged**: 2026-09-11T22:20:00+08:00
**Severity**: medium（推送流程中断，非代码问题）
**Status**: resolved（重试成功）

### Symptom
`.venv/Scripts/python.exe .workbuddy/scripts/push_github.py` 走到 fetch 阶段失败：
```
验证 Code - Insiders acct=happye scopes=[read:user,user:email,repo,workflow] -> OK
FETCH FAIL: fatal: unable to access 'https://github.com/happye/MuyunTradeAssistant.git/':
  TLS connect error: error:0A000126:SSL routines::unexpected eof while reading
```
令牌解密/验证全 OK，仅网络层断。

### Root Cause
直连 github.com 的 TLS 握手被中断（瞬时）。注意 **不是代理配置问题**：
`git config` 里 URL 级代理 `http.https://github.com.proxy = http://127.0.0.1:7890` 存在，
且 7890 端口当时**可连接**；`push_github.py` 的 `git_with_token` 只注入凭据 helper、
不碰代理配置。

### Fix
直接重跑同一脚本即成功（`b8a915f..59b31e9 main -> main`）。

### Prevention
- 看到 `TLS connect error / unexpected eof` ≠ 凭据或代理坏了：**先重试一次**，别急着改 git 配置
- 30 秒诊断法（比改配置快）：python 分别测 ① 直连 `socket+ssl` 到 github.com:443；② 经 `127.0.0.1:7890` 访问 `https://api.github.com/rate_limit`。两条任一通 = 重试即可
- 顺手可判仓库可见性：经代理**不带凭据**访问 `https://github.com/<owner>/<repo>`，返回 404 = 私有仓库（本项目即私有）

### Metadata
- Reproducible: no（瞬时网络抖动）
- Related Files: .workbuddy/scripts/push_github.py
- Tags: git-push, tls, transient, retry, proxy

## [ERR-20260925-001] 只读架构探针的解释器访问与输出编码

**Logged**: 2026-09-25
**Status**: resolved（探针执行环境处理；非产品修复）

沙箱内`.venv/Scripts/python.exe -`无法启动其引用的uv解释器；经自动审批允许后，同一仅用标准库的只读AST探针在沙箱外成功。未重装依赖或改PATH。第一次中文stdout经工具显示乱码，改用JSON ensure_ascii=True重跑确认结果。不要将环境访问或显示编码失败当成产品回归失败。

研究网页个别PDF/DOI访问失败，改从作者机构或官方原文读取；未将失败页面当成已获取证据。详情与实际探针结果见plan/fusion/PROBES.md。

## [ERR-20260926-ARCH01] 文档补丁上下文核验与外源正文不可得

**Logged**: 2026-09-26
**Status**: resolved（补丁重读真实标题后成功；外源缺正文保留为研究限制）

一次多文件 apply_patch 使用未核实的占位标题 `#`，上下文校验失败，未写入该批改动；读取各文件真实标题后重新应用成功。以后补丁上下文只取实际读到的原文，不放待替换占位锚点。

Baostock 官方主页/API文档在本轮 web 工具仅返回 `×`，未取得字段定义；没有用搜索到的第三方转载确认全字段单位/PIT资格。待 R1 取得可核验原始定义与发行人报告。

2026-09-27复用该教训：创建iteration3父目录失败后以原生PowerShell显式建目录成功；多文件补丁报错后实际检查发现前序文件已写入，因此**失败不代表零修改，重试前逐文件核对**，不盲目重放整批。巨潮原始PDF全文抓取超时，只保留来源入口，未声称核定财报数值。

## [ERR-20260927-ARCH02] 架构记忆补丁与独立探针调用错误

**Logged**: 2026-09-27
**Status**: resolved（均未触碰产品或真实账户）

同一 `apply_patch` 内 Delete+Add 同一路径被工具拒绝，原 `plan/fusion/RESUME.md` 未改变；改为 Update 原位替换成功。第一次临时影子探针同时通过 `base` 和显式参数传 `as_of`，抛 `TypeError`；改为先更新 `base['as_of']` 再构造，实测新反例。文档自检 `git diff --check` 又发现本条与新增 learning 的 Markdown 行尾空格；移除后复验。命令/脚本失败后先核对磁盘与输出，再最小修正，不把失败当作产品缺陷或验收证据。

## [ERR-20260927-ARCH03] 独立审查运行器的 Windows 与隔离兼容

**Logged**: 2026-09-27
**Status**: resolved（最终10探针无执行错误；目标180绿）

首次深审探针因stdout默认GBK无法输出emoji而失败，且cwd仍在TemporaryDirectory内导致Windows无法清理目录；改为显式UTF-8并用contextlib.chdir保证先恢复cwd再清理。首次pytest隔离门禁阻断pandas导入时stdlib的Windows版本探测子进程，先在门禁前预热platform.uname（只读），产品运行仍禁止子进程；随后语料测试试图往tests/artifacts写报告被正确拦截，运行器按模块重定向ARTIFACT到临时根；pytest默认日志NUL也被严格写门禁拦截，改为显式临时日志路径。最终两种运行器成功，真实portfolio及bak哈希不变。没有为通过测试开放真实账户或仓库写入。

另一次读取错写src/core/research_snapshot.py（实际src/data）后用rg --files定位；网页automations.md获取失败后改读官方HTML。工具失败不计产品缺陷、不据失败页面推断官方功能。

## [ERR-20261001-ARCH04] 审批探针的CLI输入格式与输出隔离

**Logged**: 2026-10-01
**Status**: resolved（最终5场景均复现且无probe_error；JSON可完整解析）

第一次V4把应用服务嵌套documents形态当作CLI claims文件形态，入口正确拒收导致探针未获得回执；读实际_load_research_claims_file后改用其扁平输入。随后CLI --json直接print未被Rich console替身截获，导致结果文件含两份JSON；以redirect_stdout单独捕获受测命令后重跑，主结果为单份JSON。两项均为审查脚本错误，未计入产品缺陷。另一次凭记忆读取不存在test_k1_observation_v6.py，rg确认实际test_k1_shadow_v6.py后继续；今后先枚举文件再读取。

## [ERR-20261001-HERED01] bash_heredoc_python_patch

**Logged**: 2026-10-01T12:00:00+08:00
**Priority**: high
**Status**: resolved
**Area**: config

### Summary
同一会话内第三次踩 heredoc 陷阱：bash heredoc 内嵌 python 补丁因转义/替换串不匹配静默失效（一次 NameError：辅助函数定义替换未生效导致调用悬空；一次多行 old_string 含反斜杠续行未匹配）。memory 与纪律均有警告仍复发。

### Error
```
NameError: name '_assert_files_in_tmp' is not defined
（另一次：str.replace 的 old_string 含 "\\" 续行符，heredoc 传给 python 后与文件实际内容不符，replace 静默 no-op）
```

### Context
- 命令：`./.venv/Scripts/python.exe - <<'EOF'` 形式跑多行 str.replace 补丁
- 环境：Windows Git Bash + 中文路径；quoted heredoc 阻止了变量展开但未阻止行内转义歧义

### Suggested Fix
本环境**一律用 Write 工具写独立 .py 补丁脚本再执行**，禁止 heredoc 内嵌 python 多行补丁（memory feedback_heredoc_escaping 升级为强制规则）；补丁脚本内做 `assert old in src` 防静默 no-op，replace 计数打点。

### Metadata
- Reproducible: yes
- Related Files: tests/core/test_k1_shadow_v6.py
- See Also: LRN-20260924-014（subprocess encoding 同族环境陷阱）

---

## [ERR-20261001-R13-TOOLS] 验证工具构造错误（已纠正）

**Logged**: 2026-10-01
**Priority**: low
**Status**: resolved
**Area**: tests

R13准备时functions JavaScript字符串引号构造失败；目标runner误写不存在的test_decision_policy（实际test_horizon_policy）；探针误用MarketState.SHOCK及在tools模块patch局部导入formatter函数。最终已逐项核对生产定义改为TRANSITION/formatter模块，381目标绿，17合同探针均进入真实行为断言，无ImportError/AttributeError混入产品缺陷计数。文档工具一次delete+add同路径patch被拒，改成受检内容更新，未丢文件。

防复发：组装目标集前按rg结果核对存在性；替身前定位真实定义/导入绑定；先判断失败位于构造还是行为断言。真实账户/知识库保护检查均未变。产品剩余问题见R13，不由工具修正冒充关闭。

---

## [ERR-20261002-R14-PROBE] 探针模型导入及必填字段（已纠正）

**Status**: resolved
**Priority**: low

R14新增LONG探针初版误从decision_contract导入HorizonPlan、继而遗漏plan_id/intent。已按decision_policy定义纠正；最终日志5个失败均为业务断言，构造错误不计产品缺陷。新探针先核对真实类定义/必填字段，不能仅靠子Agent给出的概念输入直接构造。

---

## [ERR-20261009-AUTHORITY] 弱证据越权与跨层资格失守（已关闭）

**Status**: resolved（ISS-117于2026-10-13完成原计划S2a/S2b/S3并经独立只读复核） | **Priority**: high

原专项发现否定公告、缺失数据、零置信度AI及未核验身份可能跨层获得动作权限。此前只读复核未覆盖后续行动说明/独立止盈抑制反例；2026-10-14限定P0/P1/P2补修通过，父Agent全量1962通过且独立Sol/high核对源码与实际日志后PASS，见[R15](../plan/fusion/iteration9/R15_ACCEPTANCE.md)。K1 shadow_v14冻结，后续M2有限只读接线另有1997全量/68定向及独立核7指纹通过；capture_only及G3真实研究/外源/投资效果边界保留。原范围证据见[S2AC验收](../plan/fusion/signal_authority_review/S2AC_ACCEPTANCE.md)。
## [ERR-20261015-G3] G3增量审查（分项状态见当前第九轮清单）

复现快照HEAD `28b02e1`：原两个裁剪quote漏洞已独立关闭，但数值文本200/结构100/原文100、利润冒充净利润、后置“上述收入仅为”范围三项仍FACT_CHECKED；父隔离临时反例3/3业务断言失败。另独立临时根确认同一时刻不同offset改变摘要/asm/candidate、同捕获跨期限多次now导致状态与binding分裂；Astra确认综合方法/报告旧effective隔离缺口。六项P1集中在[当前计划](../plan/fusion/iteration9/DELIVERY_PLAN.md)G3-R01–R06，不以本记录代替修复。

父10文件联合206 passed/2 failed；两失败是公开完整链正例缺新条件参数，按G3-R07显式补齐，保留旧文字UNKNOWN反例。原始证据在现有TARGETED_TEST_RESULTS_P.txt。初次Temp外部探针收集遇到不存在的其他临时路径报1 error，未归因，不计业务失败；明确rootdir/confcutdir后成功复现三项。所有父运行外源/子进程/真实状态尝试0，7保护hash与知识库集合不变。实施未验收、全量未跑，旧已验范围不重批。

cc31524增量：父62项目标通过、原三例转绿；独立复验仍发现R01词元/符号/非元单位缺口、R03未知子句回退丢限定/条件/否定及“该收入仅为”绕过。残留合回原两组，不新增问题编号；R02指定反例限定关闭，不能把局部修复升为整批接收。

3b4f07e审批更新：原点名反例已修，但父独立复现2,100→100、万元→万股/%、后置“仅包含”仍FACT_CHECKED；真实旧rule捕获记录不改即进新报告分母。全量2106/15中6红是shadow版本/执行状态产品问题，9红需补当前合法fixture前提。行业真实工具→ChatAgent13334→3918字丢全局不完整提示。输入/修法统一收口原R01/R03/R06和第九轮联验段，未批准整批。TEMP父复跑首次stderr为GBK导致解码错误，统一子进程stdout/stderr及PYTHONIOENCODING后成功；工具错误不计业务红例。

2026-10-16最终更新：上述本批工程缺口已修并限定接收，2261全量/547定向分别通过，受验SHA与独立证据见R15，不重写旧失败快照。真实年报数值格式问题另以完整Decimal词元修复；剩余未支持范围语法保持摘录，MID未建立，不把输入不足登记成交易bug。

## [ERR-20261016-VALIDATION-ENTRY] 测试集合和探针前提必须核实

**Status**: resolved | **Priority**: medium

临时整合脚本把未传tests的guard运行命名FULL_T01，实际是runner默认目标集471项。保留原日志并追加勘误；真正全量显式传tests，FULL_T01_FINAL为2261/0/2skip/1external。临时脚本已加full标签必须显式tests检查，恢复文档也给出完整入口，防止误报计数。

真实原件探针初次用早于CLI实际评估的查询时点，重启只读合理地显示待复核；改为使用实际公开返回的同as_of后，assessment身份与UNESTABLISHED一致。探针错误的嵌套claims格式、缺TradePlan必填/信号槽和Windows平台/NUL启动前提均先修构造再判业务失败；零外联/真实状态守卫不撤销。UI复核者已回传最终PASS与证据，结束turn随后因额度失败，据实记录而不冒称正常完成另一轮全量。

## [ERR-20261016-T01-T04] 本轮原型前提与测试注入（已纠正）

**Status**: resolved | **Priority**: medium

T01首轮83绿1红是fixture把NAV设为当天尚未来到的10:00；固定历史估值时点后84绿，未放宽资格门。独立新增归一别名覆盖/负数量/NaN现金为真实入口问题，边界11红集中修复后五文件101绿，再经17正式边界+10独立格及父全量2301/目标290通过。三类错误不混称投资策略缺陷。

T04首129绿1红是新shared配置stub漏接wrapper转发config_path=None，异常被业务吞掉；修签名并锁实际查询/下游次数后130绿，未弱化expected。执行者将日志误放仓库Temp，两份原件保留并精确复制到系统Temp供既有日志归档；可选移动/清理操作被自动策略拒绝后改为只复制，未绕过拒绝删文件，也不影响产品验证。保护账户/知识库不变。

## [ERR-20261016-GROK-AUDIT] 测量不能代替修复，验证资产也要核前提

**Status**: resolved | **Priority**: medium

Grok102份T05正式测试未改生产，还将future-cache行为锁正例；本轮以真实未来时点与部分写失败先红后修，独立审查/2730全量通过。G4原型漏期末相对截止门，4正式反例先红、修后49正式/2独立反例通过。独立混Temp与仓库目标收集曾报FileNotFoundError，拆轮复跑通过，原日志保留，不充业务红。主控一次只回显运行工具output漏报后台session_id，改以唯一receipt监视，没有重复开全量。逐文件HEAD实核T05为16332行，纠正定位估数14634；Windows rg路径glob及git ls-tree通配无结果已改为明确目录枚举，不把空结果当验证通过。

## [ERR-20261016-G4-SIDEBAR] 文本出处遗漏与测试定位前提

**Status**: resolved | **Priority**: medium

首轮新命令测试错以为datetime为CLI模块属性，实际函数局部import；按真实命令时钟修fixture后通过，未放宽期限。独立审发现JSON有引用但文本只写“核对所列原件”，真实合同1红落正式，补≤2份留样编号、剩余入口及中文隔离原因后20独立增量/2749全量通过。第一全量2749不覆盖该尾项，第二全量绑定补后SHA，原日志均保留。一次读取误指scripts/review_test_runner.py已改为现有iteration9入口，不计产品失败。


### ERR-20261016-FIN-IDENTITY｜响应行借请求代码自证主体

Status: resolved_new_capture_only。请求600519/指定季度，替身返回别股或错期，旧get_financial_quarterly仍产生请求股证据；正式修前93红。统一核返回证券/交易所和完整精确季末后，raw/索引/记录同源；父审补规范别名skip，防修后重复请求。最终272定向、独立122及2844全量过门，7保护hash稳定。旧raw不补证，供应商版本/单位映射/评估/接受态不变，详见现有R15及ISS-119。


### ERR-20261017-CASH-INTAKE｜公开入口与不可变回执尾项

Status: resolved_v39_intake_only。公开导入分支修前2红（误入旧研究并产生4次被阻断网络尝试）；路径引号/反斜杠、多空格及Decimal超过28位的冲突指纹均落正式反例。独立静态发现as_of派生supplier状态污染材料ID、回读未重判未来、撞同名文件无界读和提取失败无具体原因，正式8红后集中修复，最终79目标通过；父9文件262及2889全量通过。坏原件不授资格、旧接受态/默认保持。

验证资产问题另列：Temp与仓库混合目标导致收集FileNotFoundError，不算产品红；Windows CPython subprocess审计实际是executable=None和list2cmdline字符串，按list比较的临时白名单阻断合法worker，6项已验、2项未验，须按本机代码修正紧控入口再补两项。不能将注入提取器/worker未启动称为真实PDF通过。首次无效append patch无文件变化，已改为明确有标记的幂等追加。原始日志都保留在现有TARGETED，不回填假绿。

补核完成：临时Windows白名单核精确venv默认None/完整list2cmdline及SHA，08真worker通过；01只读断言误含普通research原评估/草稿写，按既有合同改为getter和cash子树后仅补01通过。分轮6+1+1关闭8独立项，真实worker共5次；未改产品或原guard来迎合探针，证据见原R15/index。


### ERR-20261017-CASH-NUMERIC｜真实表头、并发与短上下文先红后修

Status: resolved_v40_local_numeric_only。首入口1红；真实PDF单位上下文1红/93绿，修“编制单位”误匹配后通过；并发barrier首轮缺追溯字段、确定性两线程再次复现同proof双展示，各1红；context资源组1红/11绿/41未选，增加存前200词元/系数及400指数预算。最终98正式、3次实际worker、父172/独立8及2942全量通过，旧资格不晋级。

实施者未将5个红执行即时写独立文件，只有原runtime工具输出。主控从真实session恢复5个原custom_tool_call_output事件逐字归档到既有TARGETED（恢复件SHA e007acd3…），没有重跑或补造红日志；本轮v41派发已明确红绿执行开始就保存完整日志。PDF skill旧缓存路径已迁新版本，限定目录定位到26.1015.12040后用现有Poppler只读渲染；原PDF未改。视觉页号前两张需08/09补零，按实际文件名修读取，不把工具文件定位错误当数据问题。

### ERR-20261017-DSH-SDK-RESUME｜跨进程同会话实测失败

Status: investigating。现有Python SDK在同一运行实例内可续接，但关闭运行时后直接以同一home/session_id调用run，SDKserver仍执行agents.create，抛session already exists。第一轮真实开发成功、父7项通过，第二轮在模型请求前失败；不能以Fake回执仅核session_id相等冒称恢复通过。原始红证据保留在G:/Tools/DeepseekHarness/codex-dsh/tasks/d4175bb5d27a4545b7d4030da948087b/worker.log及round-2记录；原session与首轮receipt未删除。当前裁决是评估默认关闭、显式启用的公开agents.resume路径，未将新会话+文本回放冒充续接。

### ERR-20261020-LOCAL-AUTH-HELPER｜脚本生成与启动先后校验

Status: resolved_helper_ui_only。JS String.raw内嵌PowerShell的反引号换行符导致模板提前结束，脚本未生成而后续Start-Process仍执行；未发生原生认证或模型请求。改为直接补丁写独立脚本、UTF8 BOM、PowerShell AST零错后再启动，不再嵌套模板。Start-Process Hidden连首次WinForms对话框也隐藏，PID存活不证明用户可见；已用user32按本次PID和精确标题仅显示自有认证窗口，IsWindowVisible实际为true。后续此类用户交互助手用正常窗口启动，不套后台服务Hidden规则；仅语法/窗口通过，不证明已认证或DSH协作通过。

### ERR-20261020-DSH-CWD-SPELLING｜工作区注册与session路径字面值冲突

Status: current_binding_resolved_tool_gap_open。CLI以G:/创建session，workspace/create返回fs.realpath规范G:\路径，随后session/create(workspaceId)在ensureSession的原始cwd相等门拒绝RPC；控制器只暴露RPC_REJECTED。该空task未发送模型请求，保留不删除。新建规范cwd的task后正常API绑定与成员回读成功，未修改部署、旧session头或绕门。拒绝后的Node25进程还出现UV_HANDLE_CLOSING断言，单次观察不足定根因；工具长期待办分别跟踪路径封装和错误诊断，不写成已修源码。

### ERR-20261020-CLEAN-INTEGRATION｜公开夹具缺失与Windows隔离路径误拒

Status: resolved_in_integration_final_full_3091_passed。临时detached合并树全量3082通过/3失败：两例因5份公开PDF只存在原工作区ignored目录，一例为同临时根并发mkdir被路径判断拒绝。5份PDF已逐个核既有manifest的SHA后精确补齐，63项相关测试通过；同根被拒的Windows扩展前缀机制通过真实ntpath分支替身复现，1红→51绿。原一次失败未记录resolved值，不能追溯证明其具体前缀。最终全量及停止指纹见本轮R15/REVIEW_INDEX与既有日志；不删失败证据、不开放真实状态写入。


### ERR-20261020-CONTINUATION-TOOLS

Status: resolved_for_this_continuation / low. 当前运行时未暴露read_thread/create_thread，不能读取引用聊天或创建Codex聊天；已明示限制，按磁盘RESUME续正式DSH任务，不推定聊天内容。Git直连21秒失败，命令级http.proxy及http.https://github.com.proxy指向现有127.0.0.1:7890后核远端与本地bd9e5d0一致，不改全局配置。一次错误写为https://github.com.proxy被Git拒绝，已纠正键名。REVIEW_INDEX是JSON不是Markdown；查询先rg --files，不猜路径。


### ERR-20261021-T05-REVIEW-GUARD

Status: resolved / parent temporary review fixture only。独立退出复验先3例误拒NUL stdin设备（admitted child=0），再3例误认Windows subprocess审计的list2cmdline字符串为列表（admitted child=0）。仅修OS Temp父夹具：精确NUL别名、精确重建命令字符串；最终3通过，admitted children=3，其他外联/账户0，7源码pins和8保护hash不变。两次夹具失败原始日志保留，未改产品或普通review_test_runner。


### ERR-20261024-NESTED-ABANDON｜外层超时内层迟到缓存仍写

基线2727141：call_with_timeout(outer,.05s)，outer另起inner(2s)，等待外层TimeoutError后释放inner；inner is_call_abandoned为False。正式pytest -k nested：8 failed/4 passed/10 deselected（3.25s），四交叉/三层/三真实缓存负例红；正常与兄弟正例保留。T05-E3实现后受影响三文件68 passed/8 skipped（11.50s），最终验收见第十轮记录。原始日志归档到现有TARGETED/FULL，不抹红。


### ERR-20261025-VALIDATOR-ISOLATION｜两类验证设施失败不可伪称产品全绿

首次3506 setup errors（140.24s）由旧索引只读门阻断，无正文；完整8182127字节原件gzip保真。修复门后全量3514 passed/2 failed（550.35s），二者均测试写出隔离临时根。保留失败，测试改用tmp_path后两文件10 passed（3.77s）。最后只修改两测试局部body和docstring，528项及全局AST未变，独立复核后增量接收，无最终树一次全绿全量；边界见T05E3_ACCEPTANCE.json。


### ERR-20261025-THEME-QUERY-OUTSIDE-BUDGET｜主题代码首包在15秒门外

旧_verify_code的query/error/fields在调用线程，只有next读取受限；有限阻塞反例6 failed/9 passed（7.71s），其中query/error/fields三例直接证明未及时返回。修复后15 passed（1.00s），主控相关204 passed（3.64s）。不改登录，不声称单候选全程或整批15秒，不修共享会话；原始红绿及审查见既有TARGETED和T05F1_ACCEPTANCE.json。
