# Feature Requests

Capabilities requested by the user.

---
## [FEAT-20260723-001] chat_new_machine_init

**Logged**: 2026-07-23
**Priority**: high
**Status**: pending
**Area**: backend

### Requested Capability
chat 命令在新机器第一次初始化时，检查缺失的依赖/模型/配置，给清晰提示+安装命令，而非直接崩溃。

### User Context
开发机已装好依赖（.venv/RAG模型/API key），但新机器第一次跑 chat 会因缺 openai/jieba/sentence-transformers/faiss/RAG索引/API key 而失败（ImportError/ModelNotFound/无key）。开发阶段测试不暴露，新机器部署才暴露。

### Complexity Estimate
medium

### Suggested Implementation
chat 启动前加 _preflight_check():
1. 检查 .venv（uv sync）
2. 检查 openai/jieba/faiss 可 import
3. 检查 RAG 模型（BAAI/bge-small-zh-v1.5）+ 索引（knowledge/）
4. 检查 API key（settings.local.yaml / 环境变量）
5. 缺失给清晰提示+安装命令（uv sync / 配 key / build RAG）

### Metadata
- Frequency: first_time
- Related Features: chat_agent, RAG

## [FEAT-20260816-001] benzong_prosperity_data_hookup

**Logged**: 2026-08-16
**Priority**: high
**Status**: pending
**Area**: backend

### Requested Capability
笨总评分 industry_prosperity 维度接入行业数据层（industry_data.py），把"看新闻标题猜景气"升级为"看价格分位+库存方向+需求增速"。

### User Context
ISS-055 定调"闸门立在最软的柱子上"；2026-07-17 调研报告方案 A 本要的接口恰好在 ISS-061 已全部落地。评估见 docs/2026-08-16_行业数据层跨模块适用性评估.md。

### Complexity Estimate
medium

### Suggested Implementation
data_provider.get_data_summary 加 industry_data 字段（复用 build_industry_report 三个 section 文本）-> industry_prosperity.prompt 注入。【红线】必须配回测对比 + bump benzong CACHE_VERSION + 不动 effective_grade 公式 + 兜底条件变严（四项缺一不可，见评估文档第三节）。

### Metadata
- Frequency: planned
- Related Features: benzong_scoring, industry_data
- Blocks: 5年回测完成后实施（回测是验收载体）

---

## [FEAT-20260816-002] chat_streaming_output

**Logged**: 2026-08-16
**Priority**: medium
**Status**: pending
**Area**: backend

### Requested Capability
chat 最终回答 token 级流式输出（当前是生成完一次性打印，长回答等待感强；工具调用进度已是流式）。

### User Context
工具进度流式已做（ISS-059），最终回答流式两次提及未拍板。

### Complexity Estimate
medium

### Suggested Implementation
_call_api 加 stream=True 路径（非工具轮），与 max_tokens 降级重试逻辑交织需谨慎：流式失败回退非流式。验收：长回答逐字输出+截断检测仍生效。

### Metadata
- Frequency: recurring
- Related Features: chat_agent
