---
name: muyun-dev-discipline
description: "暮云思辨投资助手 的工程纪律门禁（工具中立版）。Use when: (1) 修复任何 bug 之前/之后，(2) 提交 commit 之前，(3) 准备写文档或注释来「记录」一个问题而不是修它，(4) 会话结束前需要沉淀教训，(5) 准备新增功能或跑新一轮调参/回测，(6) 接手新会话需要了解本项目反复踩的坑。防止本项目特有的五类复发：知识通道断裂、只修被点名那一处、文档冒充修复、审查不落测试、教训写了不执行。"
agent_created: true
---

# 暮云开发纪律门禁

> 来源：2026-08-29 两轮对抗性审查（69 条发现）+ 259 条 git 提交考古
> 完整论证见项目根 `开发问题根因复盘_20260829.md`
> **本 skill 工具中立**：主副本在 `skills/muyun-dev-discipline/SKILL.md`，各 Agent 工具目录下有同步副本（见 §0）

## §0 先说清楚：这个 skill 为什么存在多份副本

不同 Agent 工具扫描 skill 的目录**各不相同**，且互不兼容：

| 工具 | 自动发现的 skill 目录 |
|---|---|
| Claude Code | `.claude/skills/` |
| VS Code Copilot | `.github/skills/` |
| Cursor | `.cursor/rules/` |
| Codex / OpenAI | `.codex/skills/` |
| WorkBuddy / CodeBuddy | `.workbuddy/skills/` |

**本项目踩过的坑**：自我提升 skill 只放在 `.github/skills/`，而实际干活的 Agent 不扫那个目录 →
`copilot-instructions.md` 里那条「失败后必须先走 self-improvement skill」的强制规则**从未执行过一次**。

**所以纪律**：

1. **主副本 = `skills/muyun-dev-discipline/SKILL.md`**（项目根，中立，任何工具都能读）
2. **各工具目录放同步副本**，改动后跑一次：
   ```bash
   bash scripts/sync-agent-skills.sh
   ```
3. **不要依赖"某个目录一定会被加载"**。真正可靠的兜底是：
   在各工具的**入口指令文件**（`AGENTS.md` / `CLAUDE.md` / `.github/copilot-instructions.md` / `.cursorrules`）里
   显式写死一句「开工前先读 `skills/muyun-dev-discipline/SKILL.md`」。
   这些入口文件的覆盖面远大于 skill 目录的自动发现。

---

## 铁律 0：教训必须落进仓库内（跨工具唯一可靠的位置）

会话级记忆目录**因工具而异且大多不进版本控制**（有的工具甚至没有持久记忆）。
**唯一跨工具、跨机器、跨会话可靠的沉淀位置 = 仓库内的 `.learnings/`。**

| 位置 | 跨工具 | 版本控制 | 用途 |
|---|---|---|---|
| `.learnings/LEARNINGS.md` | ✅ | ✅ | **唯一可靠沉淀点，必须写** |
| `.learnings/ERRORS.md` | ✅ | ✅ | 失败与复现条件 |
| 工具自带的会话记忆目录 | ❌ | ❌ | 可选，只服务当前会话 |

**只写工具自带的记忆目录 = 等于没写**（换工具 / 换机器 / 清缓存即归零）。

> 本项目实证：`.learnings/LEARNINGS.md` 停在 2026-08-25、`ERRORS.md` 停在 07-29，
> 而同期两轮审查的 69 条发现**零条进入**；23 条 LRN 里 13 条 Status 永远 pending。

---

## 铁律 1：修 bug 必须走四步，缺一步不许提交

```
① 写回归测试（先红灯）→ ② 修（转绿灯）→ ③ 扫同类点 → ④ 落 .learnings/
```

### ③ 扫同类点（本项目最容易漏的一步）

修完不要直接提交。先跑全局扫描，报告剩余数量：

```bash
grep -rn "with ThreadPoolExecutor" src/     # 只改了 data_provider，漏了 akshare_client
grep -rn "HF_ENDPOINT" src/                # 修了 3 次仍复发第 4 次
grep -rn "0\.8\.7\.[0-9]" start.py src/cli/main.py AGENTS.md README.md docs/
```

**规则：扫出来还有同类点 → 要么一起修，要么在 ISSUES.md 明确记录「已知剩余 N 处，本轮不修」，不许默默留着。**

### 本项目已确认的同类点扫描清单

| 模式 | 意思 |
|---|---|
| `with ThreadPoolExecutor` | `__exit__` 的 `shutdown(wait=True)` 会 join 卡死线程使超时失效，必须显式 `shutdown(wait=False)` |
| `except.*:` + `pass` / `logger.debug` + `continue` | 静默 fail-open，条件不计入分母 → 规则**降档触发**（比不触发更危险） |
| `os.environ[...] = ` | 全局污染且通常不还原 → 测试顺序依赖 |
| `if <DataFrame>:` | DataFrame 真值判断抛 ambiguous |
| 中文全角 `（）` | 写进 .py 字符串直接 SyntaxError |
| `0\.8\.7\.[0-9]` | 版本号在 start.py / main.py / AGENTS.md / README.md / docs 至少 5 处，改一处要全改 |

---

## 铁律 2：文档/注释动作 ≠ 修复

**以下三种行为一律判定为「未修复」，不许标 ✅：**

1. commit message 声称改了代码，但 `git show --stat` 显示零行代码改动
   - 实证：`87047f8` 号称「HF_ENDPOINT默认改国内镜像」，实际只加了一个 76 行 .md
2. 改了 docstring / 注释让它「与行为一致」，但行为本身没改
   - 实证：`c993c6d fix(全局审查M-B): plan_guard stale docstring 对齐` → `plan_guard.py:177` 至今仍压 `take_profit_trim`
3. 把教训写进 AGENTS.md 就算修完了
   - 实证：`814963c` 把 with 块陷阱写进 AGENTS → `akshare_client.py` 那个 with 块活到 08-29

**自检查句：我的 diff 里有没有改变运行时的代码行？没有就是没修。**

提交前跑一次：

```bash
git show --stat HEAD | tail -5    # 看代码文件行数变化，别只看 message
```

---

## 铁律 3：验证靠「跑」，不靠「读」

两轮审查里最硬的几条发现，**全是跑出来的，静态审查都没看出来**：

| 手段 | 抓到的 | 为什么读代码看不出 |
|---|---|---|
| 跑全量 pytest | `embedding.py` 写全局 `os.environ` 不还原 → 测试顺序依赖 | 单看文件是「正确」的，只有一起跑才炸 |
| 跑脚本交叉比对（YAML 条件名 ↔ `CONDITION_REGISTRY`） | 9 个缺失条件 + 25 个死条件 | 14 个 YAML × 82 个注册表项，人工必然漏 |
| 跑回测打 `trade['sell_path']` | 实际卖出走 `strategy_layer._infer_sell_path`，不是 `entry_exit/exit_rules.py` | 两个模块各管一半，读代码会假设错 |

**规则：**
- 涉及全局状态（env / 类级缓存 / 单例）的改动 → **必须跑全量测试，不能只跑单文件**
- 涉及 YAML ↔ 代码映射 → **必须写脚本交叉比对，不能人工读**
- 涉及数学公式（指标 / 统计 / 归一化）→ **必须用已知输入算已知输出，不能只看代码"像对的"**
- 脚本比对出的结论 → **必须回读代码确认再报**（`price_position` 是特例分支，脚本会假阳性）

---

## 铁律 4：调参 / 回测的红线（LRN-20260619-001，写了但一直没被执行）

| 红线 | 内容 |
|---|---|
| **30 分钟前提验证** | 动手前先验证「我要改的参数实际生效几天/几次」。不生效直接放弃这个方向 |
| **第三轮失败原则** | 同一参数家族调 2 次都没显著改善 → 第 3 次直接停手，换方向或质疑架构 |
| **改善门槛** | 单股 < 2pp / 整体 < 1pp = 噪声，不算成功，不写「已解决」，不进 commit |
| **基准对照** | 只看绝对收益不看对照 = 把噪音当成功。必须同股同期 A/B |

**先打 `trade['sell_path']` 分布再动手**——回测实际卖出走 `strategy_layer.py:88-97` 的 11 个 hardcoded 阈值，不是 `entry_exit/config.yaml`。

**单股样本无发言权**：ISS-068 里柯力单股 A/B 显示 -10.9pp 吓人，全量 19 只视角下是噪声（+0.63）。组级统计才有结论权。

---

## 铁律 5：提交前自检（commit 门）

按顺序过一遍，任一不过不许提交：

- [ ] **非空校验**：`git show --stat` 里有代码改动吗？没有 → 别用 fix 前缀（铁律 2）
- [ ] **同类扫描**：铁律 1 的清单跑过了吗？剩余几处？记下来了吗？
- [ ] **验证命令**：能给出一条可复现的验证命令 + 期望输出吗？给不出 → 补
- [ ] **用户可感知**：用户跑 `start.py` 看得到这次改了什么吗？（AGENTS.md §二·五 硬条件 5 选 1）
- [ ] **版本号**：改了版本吗？改了就 5 处全改（start.py / main.py / AGENTS.md / README.md / docs）
- [ ] **ISSUES 状态**：状态变更带 `file:line` + 日期 + commit 短哈希了吗？
- [ ] **告警人话化三处同步**：新增/改了 `logger.warning` → `src/cli/plain_errors.py` + `docs/报错速查手册.md` + 代码本身
- [ ] **教训落库**：写进 `.learnings/` 了吗？（铁律 0）
- [ ] **skill 同步**：改了本 skill 吗？跑了 `scripts/sync-agent-skills.sh` 吗？（§0）

---

## 铁律 6：本项目特有的死坑（改代码前必看）

1. **execution_layer 只读 `position_action`**（无 STOP 概念）。PlanGuard 规则 / EntryExit force_exit 救回**必须设 `position_action=CLOSE_ALL`**，只设 `decision=SELL` 会**静默失效**
2. **回测调 `orchestrator.analyze` 必须传 `is_backtest=True`**（否则 future 数据前瞻污染）
3. **`StockData.recent_announcements` 只在 live builder 填，回测绝不可填**（ISS-052）
4. **`effective_grade()` 用于展示/决策，`grade()` 是 Excel 保真值锁死不能改**
5. **改笨总评分逻辑必须 bump `CACHE_VERSION`**（`src/core/benzong/cache.py` 顶部），否则旧缓存命中 →「改代码不生效」
6. **回测必须从 settings.yaml 显式传 `entry_exit_config` 给 BacktestEngine**，CLI 不兜底
7. **回测不能跑 AI**（30股×242天×6维=43560次），必须用规则版 fallback
8. **网络失败是反爬不是代理问题** → 降级 + 警告，**绝不编造数据**
9. **`subprocess.run(text=True)` 必须显式 `encoding="utf-8"`**（LRN-20260924-014）：不带 encoding 时中文 Windows 父进程按 GBK 解码子进程 UTF-8 输出 → 中文全乱码 → 靠解析输出内容的断言全灭（「一会过一会不过」先查解码码页，再谈环境状态依赖）
10. **patch 模块属性期间禁止让任何模块「首次 import」**（LRN-20260925-015）：`from X import Y` 按值绑定会把替身永久捕获进导入方命名空间（monkeypatch 只恢复被 patch 的模块属性）→ 全量绿但局部组合跑红的顺序依赖，第一嫌疑；patch 类测试文件顶部先预导入有 by-value 绑定的真实模块；且**接线测试（parse_input→run_cli 全链）与单元直调测试必须并存**——直调实现抓不到接线断裂（M6 doctor P0 实证）

完整 15 条见 `AGENTS.md` §五。

---

## 会话收尾动作

任务结束前：

1. 把本次教训写进 `.learnings/LEARNINGS.md`（**必做**，跨工具唯一可靠点）
2. 有失败/复现条件的同步进 `.learnings/ERRORS.md`
3. 若动了 ISSUES.md → 带 `file:line` + 日期 + commit 短哈希
4. 若发现了新类别的坑 → 回来更新本 skill 的「铁律 6 死坑」清单
5. 若改了本 skill → 跑 `bash scripts/sync-agent-skills.sh`
