# 第十轮：T05-D 旧并行 executor 退出界（ISS-120 剩余 4 处）

更新 2026-10-21。本文件是第十轮唯一合同与执行入口；第九轮历史合同与证据仍在 [iteration9/DELIVERY_PLAN.md](../iteration9/DELIVERY_PLAN.md)，不重算。

## 角色与分工（本轮）

- 用户 2026-10-21 指派 **Claude Code** 接手迭代开发，并授权其兼任架构师主导方案；方案必须落盘并同步推送（多机接续）。
- 本机工作区：`C:\Users\HanPeiyi\SVN\Muyun`，默认直接 main，Claude Code 主控负责 Git。
- 分工按 `CLAUDE.md`「Claude Code role routing」：muyun-coder 写测试/实现，muyun-runner 跑测试，code-quality-guard 做一次高风险独立复核（本卡属于并发/超时边界），主控整合、验收、提交。
- 原 Codex+DSH 流程（RESUME「DSH恢复身份」）保留为另一台机器的入口，本轮不使用。

## 卡片 T05-D

| 项 | 内容 |
|---|---|
| 对应 | G7 / T05；ISS-120「同类剩余4处」 |
| 消费者路径 | ① `bz`/笨总评分 → `get_data_summary` → `get_industry_metrics`（`src/core/benzong/data_provider.py`，外层 1 个 + 内层 2 个 executor）；② 扫描/全市场快照 → `MarketCache._fetch_sina_market`（`src/scanner/market_cache.py`，10 并发） |
| 问题 | `ThreadPoolExecutor.shutdown(wait=False)` 只让调用方不等，但卡住的工作线程会在解释器退出时被 `concurrent.futures` 的 atexit join。数据源超时后，`start.py`/CLI 退出可被拖住最长约 150s（行业）或 30s/页（新浪）。第九轮已对 3 个单槽 helper 证明并修复同类问题，本卡处理剩下的 4 处。 |
| 用户收益 | 行业景气或新浪快照超时降级后，退出 REPL/CLI 不再被悬挂的后台请求拖住。 |
| 修改边界 | `src/data/timeout_call.py`（新增并行版 daemon helper）、`src/core/benzong/data_provider.py`（industry_metrics 内 3 处）、`src/scanner/market_cache.py`（新浪并行 1 处）、`tests/core/test_t05_timeout_exit.py`（新增退出 case）、新增 helper 单测文件；文档：ISSUES ISS-120、本文件、STATUS/RESUME/MILESTONES 的 T05 行。 |
| 不改 | 默认超时预算（行业 150s、新浪总 45s、单页 30s）、降级文案语义、返回三态（matched/not_matched/failed）、超时即放弃整份新浪快照并换源的既有裁决、评分/阈值/CACHE_VERSION、账户、网络来源。不处理晚到写缓存与 Baostock 会话重叠（ISS-120 保留限制）。 |
| 前置 | 本机 Python 3.11+ 与 `.venv`（2026-10-21 本机缺 Python，需用户批准安装）。 |

### 设计

1. `src/data/timeout_call.py` 新增 `run_parallel_with_deadline(fns, *, max_workers, timeout)`：
   - 起 `min(max_workers, len(fns))` 个 daemon 线程从任务队列取活；结果按输入顺序返回列表。
   - 总预算到点抛 `TimeoutError` 子类，带 `completed` 计数（供新浪日志「已完成 N/73 页」）；`None` 无限等，负数/NaN 按 0（与 `call_with_timeout` 一致）。
   - 任一 `fn` 抛异常 → 原实例在调用线程重抛（与旧 `future.result()` 一致）。
   - 不 join、不全局登记；迟到结果只进本次调用的队列。线程不能强杀，副作用可能继续（同 `call_with_timeout` 限制说明）。
2. `get_industry_metrics`：外层改 `call_with_timeout(_pull, timeout=INDUSTRY_METRICS_TIMEOUT_S)`；内层三节/两节改 `run_parallel_with_deadline(..., timeout=INDUSTRY_SECTION_TIMEOUT_S)`。预算抽成模块常量，默认值不变（150.0）。`_BridgeMiss` 仍按原实例透传为 not_matched，超时仍为 failed。
3. `_fetch_sina_market`：改 `run_parallel_with_deadline`（10 并发，`MarketCache.SINA_PARALLEL_TIMEOUT_S = 45.0`），超时日志保留完成页数并返回 None 换源。结果按页序合并（旧为完成顺序，后续去重与列名标准化不依赖顺序）。

### 正常 / 失败 / 迁移

- 正常：所有节/页按时返回 → 输出与旧实现一致（新浪页序更稳定）。
- 失败：单节异常仍由 `_safe_section` 转缺失文本；超时 → failed / 换源，行为与旧一致，但解释器可立即退出。
- 迁移：无持久化格式变化，无缓存迁移。

### 反例与验收命令

先红后绿（铁律 1）：

1. 预算常量抽取（行为不变）先单独落地，使退出 case 能用小预算，红灯只因「退出被拖住」而不是「调用本身阻塞」。
2. `tests/core/test_t05_timeout_exit.py` 新增 4 个子进程 case（默认 skip，需 `MUYUN_T05_EXIT_PROBE=1`）：
   - `industry_outer`：`_pull` 本身挂起（桩 `industry_data.load_chains` 睡 8s），外层预算 0.05s；
   - `industry_chain_sections`：命中手写链，三节桩函数睡 8s；
   - `industry_commodity_sections`：L3 商品关键词路径，两节睡 8s；
   - `sina_parallel`：桩 `requests.Session.get` 睡 8s，`SINA_PARALLEL_TIMEOUT_S` 设 0.05。
   断言沿用现有夹具：调用 ≤1s 返回、语义正确（None/failed）、子进程返回后 1s 内退出、父进程观察窗口有效。旧实现应红在 EXIT ASSERTION。
3. helper 进程内单测（默认运行）：输入顺序、异常原实例重抛、超时与 `completed`、并发上限、`None`/负数/NaN 预算、空输入、线程为 daemon。
4. 验收命令（本机 venv 建好后）：
   - `.venv/Scripts/python.exe -B -X utf8 -m pytest -q tests/core/test_t05_timeout_exit.py <helper单测> -p no:cacheprovider`
   - `MUYUN_T05_EXIT_PROBE=1` 同上，只跑退出 case
   - 全量：`.venv/Scripts/python.exe -B -X utf8 -m pytest -q tests -p no:cacheprovider`，0 failed
5. 同类扫描：`grep -rn "ThreadPoolExecutor" src/` 结果应为 0（`scripts/measure_timeout_overhead.py` 是测量脚本，不在产品路径，记录不改）。

### 资源上限、停止与回滚

- 离线，零网络、零付费 AI、不读写真实账户。
- 停止条件：某站点在旧代码上**复现不出**退出阻塞 → 不改该站点，在 ISS-120 记「未复现」；若 helper 需要改动默认预算或返回语义才能通过 → 停下回到设计。
- 回滚：本卡为独立 commit，`git revert` 即可。

## 执行记录

| 日期 | 事项 | 结果 |
|---|---|---|
| 2026-10-21 | 合同落盘 | 本文件；实施等待本机 Python 环境 |
