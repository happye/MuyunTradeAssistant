# Errors

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
