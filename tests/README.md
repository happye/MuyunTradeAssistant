# tests/ 目录说明与跑法（M1，2026-09-24）

> pytest 配置在仓库根 `pytest.ini`；收集期排除与用户目录隔离在 `tests/conftest.py`。

## 目录结构

| 目录 | 用途 | 备注 |
|------|------|------|
| `tests/chat/` | chat 模块单测（纯 mock，无网络） | |
| `tests/benzong/` | 笨总评分体系（mock 为主） | |
| `tests/backtest/` | 回测与策略层 | |
| `tests/core/` | 核心引擎单测 | `test_tech_context_e2e.py` 为脚本式 E2E（默认 skip，直跑） |
| `tests/rag/` | RAG 摄取/检索/隔离/重排（mock 为主） | |
| `tests/data_sources/` | 数据源测试（mock 与真实外源混合，见下） | |
| `tests/ui/` | web/tui 集成测试（mock 底层引擎） | |
| `tests/artifacts/` | 历史回测/issue 验证的输出产物（非脚本） | 收集期排除 |
| `tests/rag_eval/` | RAG 检索质量评估工具 | 收集期排除 |

## 常用命令

```bash
# 离线全量（默认入口，M1 起的统计口径）：不碰真实 ~/.muyun、不花 AI 费用、不打真实外源
pytest -q

# 单文件 / 单用例
pytest tests/core/test_scan_review.py -q
pytest tests/core/test_scan_review.py::test_parse_and_history_roundtrip -q

# 脚本直跑入口保留（文件自带 sys.path 修复）
.\.venv\Scripts\python.exe tests\core\test_trade_plan.py
```

全量数字沿用 L05 统计口径：每次全量的 passed/skipped 写进 commit message，历史对比以此为准。

## 真实外源 / AI 显式启用（默认全部排除）

| 目标 | 命令 |
|------|------|
| 全数据源连通性体检（`test_all_api.py`） | `MUYUN_RUN_EXTERNAL=1 pytest tests/data_sources/test_all_api.py -q` |
| 数据源体检结构用例（`test_source_check.py`） | `pytest tests/data_sources/test_source_check.py -m external -q` |
| AI E2E（真实 AI，花钱） | `MUYUN_E2E_AI=1 pytest tests/core/test_tech_context_e2e.py -q` 或直接 `uv run python tests/core/test_tech_context_e2e.py` |

> `test_all_api.py` 的门是**收集期排除**而非 marker：它 import 期就清代理环境变量、
> reconfigure stdout、写证书文件，`-m` 事后过滤挡不住 import 副作用。
> 设 `MUYUN_RUN_EXTERNAL=1` 后它才被收集（其用例无 external marker，不受 addopts 的
> `-m "not external"` 影响）。

## 测试用户目录隔离（conftest.py）

- 默认（无条件下生效）：`HOME` / `USERPROFILE` 在**任何 src import 之前**指向
  `tempfile.mkdtemp(prefix="muyun-test-home-")`，src 各模块 import 时经 `Path.home()`
  解析的状态文件（`~/.muyun/*` 等）全部落进临时目录，进程退出自动清理。
  子进程测试（如 `test_indicator_math_script`）经 `env=dict(os.environ)` 自然继承。
- 测试自身的 monkeypatch / `_swap` 路径替身依然优先——conftest 不做任何 fixture 级路径重设
  （架构师试作教训：根 autouse fixture 强制重设会覆盖 test_chat_command_bridge 的替身）。
- 逃生口 `MUYUN_TESTS_REAL_HOME=1`：仅诊断隔离问题用（会碰真实 home，勿常开）。

## 标记（pytest.ini 注册）

- `external`：打真实外部数据源/网络。默认被 `addopts = -m "not external"` 排除。
- `ai`：调用真实 AI 接口（花钱）。现有用例另有 `MUYUN_E2E_AI` 门自行 skip。
