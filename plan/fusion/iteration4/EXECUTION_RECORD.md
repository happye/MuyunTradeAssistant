# 第四轮 K 系列执行账本（实施方 Claude Code）

> 接手基线：`7922433`（= `def45d1` + 两笔 docs 交接提交，产品代码与 REVIEW_INDEX 指纹一致）
> 开工核对（2026-09-28）：14 个源码指纹全部匹配 REVIEW_INDEX.json；探针 10/10 `observed_defect=true` 复现；
> 工作树仅用户本地状态（portfolio.yaml 等，不碰）。架构师=Codex（不提交 Git，新交付由实施方代提交）。
> 纪律：每个反例先写独立测试红灯 → 修转绿 → 扫同类点 → 落 `.learnings/`；告警三处同步；
> 隔离断言全路径；`portfolio.yaml` 永不提交。

## 执行顺序（交接 §4）

K0a 账户止血 → K0b 事实/命题 → K0c 计划/观察 → K2a 公开研究闭环 →（K1 冻结）→ K2b 归并 `l`；
K3 可与 K0 并行；K4 收尾。

## K0a｜账户止血（D2/D3/D4/D7）

**状态：实施完成，全量验证中（2026-09-28）**

### 子任务拆解

| # | 内容 | 反例 | 状态 |
|---|---|---|---|
| K0a-T1 | 红灯测试：tests/core/test_k0a_account_integrity.py（D2/D7，10 例）+ test_k0a_projection_intent.py（D3/D4，6 例） | 全部 | ✅ 红灯确认（11 failed 后实施） |
| K0a-T2 | D2 完整性先于写入：account_snapshot `_load_detailed`（字节级、行号/偏移/sha256 结构化损坏留痕）+ `_confirm_locked`/`_append_opening_locked` 写完整性门 + 回执扩 `PERSIST_UNKNOWN`（fsync 失败≠成功）+ append OSError 零写入诚实回执 | D2 | ✅ |
| K0a-T3 | D7 现金口径：cash_deployable=净额（reserved 仅解释）；ACCOUNT_SNAPSHOT_VERSION r5.v1→k0a.v1；today 文案同步 | D7 | ✅ |
| K0a-T4 | D3 投影水位：`applied_fills` 与持仓值同次 `_save` 原子落盘；confirm_fill 崩溃恢复三态（水位∧账本）；apply_quantity_fill 同机制；pick_reusable_fill_id 消费判定=账本∧水位 | D3 | ✅ |
| K0a-T5 | D4 数量事实：CLI qty 路径改 `apply_quantity_fill`（事实来自账本快照：已成交/剩余、批次均价成本、真实成交日贯穿、ratio_stale+quantity_fact 留痕；部分卖出保留记录不虚判清仓；建议只在事实达成时 CONFIRMED 否则待确认+偏离注记+事件标 deviation）；replay 派生 avg_cost | D4 | ✅ |
| K0a-T6 | 旧预期改写（锁了缺陷行为）：test_j2_account_service.py::test_truncated_last_line_isolated_rebuild（旧口径锁了"坏尾后 ACCEPTED"）、test_account_snapshot.py::test_reserved_cash_not_double_counted（旧口径锁了 reserved 二次扣减） | — | ✅ |
| K0a-T7 | 验收：目标测试 65 绿；架构师探针 D2/D3/D4/D7 → ok（未复现，探针文件未改）；全量 pytest；隔离 REPL 纵向 e2e（parse_input→run_cli→账本→重启→today/legacy 读取） | — | ✅（全量见下） |

### 决策记录（K0a）

- D2 语义：最后一条非空行损坏=尾部损坏 → 拒绝一切新追加（原件不动，回执给行号/偏移/hash）；
  中部坏行只隔离不冻结写入；完整 JSON 缺尾换行 ≠ 损坏，追加前补换行。行分隔与 Python
  universal newline 对齐（\r\n、\r、\n）——**旧实现靠 read_text 的 lone-\r 分隔碰巧没丢数据，
  字节级重写后必须显式对齐**。
- 幂等/冲突/STALE 判定是只读操作，尾部损坏时仍可返回（重试可核对）；新写入前才过完整性门。
- 回执状态集扩为 ACCEPTED/DUPLICATE/CONFLICT/REJECTED/STALE/PERSIST_UNKNOWN（fsync 失败≠普通成功；
  重跑同一确认命中 event_id 幂等返回 DUPLICATE，可安全核对）。
- D3 语义：投影消费标记（applied_fills）与持仓值**同一次 _save** 落盘（「投影写后/水位前」结构上
  不可出现）；投影已消费 → 只补账本；账本有/水位无（无水位时代的历史数据）→ **视为已应用**
  （旧写入顺序=持仓先账本后）+ 回填水位，不重放；两者齐 → 幂等跳过。
- D4 语义：数量确认后比例视图不按建议全额反推；部分卖出保留记录+ratio_stale+quantity_fact
  （{quantity, as_of, avg_cost}）；只有「剩余 0 股」这个事实才触发清仓语义；建议只在事实达成
  （清仓完成）时 CONFIRMED，否则保持待确认+偏离注记；CLOSE_ALL 部分卖的事件标
  deviation_from_proposal=True；BUY 加仓成本取批次均价（replay 派生 avg_cost），不用最新买价
  覆盖 entry_price。
- pick_reusable_fill_id 的「投影未消费」判定升级为 账本∧水位（两处齐才算消费完成）——
  数量路径写序=账本先投影后，与比例路径相反，故 has_fill_fn 语义按路径分别接线。
- 比例路径（不带 --qty）语义不变（RATIO_ONLY 旧口径保留，输出仍提示补 --qty 可入数量账本）。

### 同类点扫描（铁律1③）

- `has_fill` 消费者：全部在已设计路径内（confirm_fill/apply_quantity_fill/pick_reusable 接线点）。
- `target-current` 反推：仅存于纯比例路径（main.py:2301，已注明「仅比例路径使用」）与
  pm 内部「已达成 new_ratio vs 建议 target」的合法比较方向。
- `cash_deployable`/reserved 二次扣减：仅定义处+allocate+today，全部按净额口径统一。

### 验证记录

| 验证 | 命令 | 结果 |
|---|---|---|
| 目标测试 | `pytest tests/core/test_k0a_*.py tests/core/test_j2_account_service.py tests/core/test_account_snapshot.py tests/core/test_confirmed_fill.py -q` | **65 passed** |
| 架构师探针 | `python -B plan/fusion/iteration4/deep_review_probes.py`（未改动） | D2/D3/D4/D7 → **ok（不再复现）**；D1×4/D5/D6 → DEFECT（K0b 范围，未修） |
| 全量 | `pytest -q`（后台跑） | 见下方补记 |
| 纵向 e2e | tests/core/test_k0a_vertical_e2e.py（真实 parse_input→run_cli→账本→重启→today） | ✅ |

### 告警三处同步（K0a）

plain_errors.py 新增 6 条 + 更新 fsync 条目：账户账本存在/账户账本损坏段隔离/账本追加失败/
账本完整性兜底拒绝追加/fill 消费水位回填失败/成交账本补记写入失败；docs/报错速查手册.md
同步更新（N+1 建议账本组 + 账户事件账本组）。
