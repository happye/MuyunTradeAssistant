# AI Investment Advisor System - Architecture & Development Specification

## Version
v1.0  
Generated: 2026-04-05T13:55:37.700360 UTC

---

# 1. Objective

Design a **highly extensible, maintainable, and stable AI-assisted investment decision system** that transforms investment methodology into executable logic.

---

# 2. Core Principles

- Deterministic > Generative
- Modular architecture
- Clear separation of concerns
- Observable & debuggable decision flow
- Strategy = Data + Logic (not prompt)

---

# 3. System Architecture

## 3.1 High-Level Layers

1. Data Layer (RAG + Market Data)
2. Logic Layer (Skill Engine)
3. Decision Layer (Scoring + State Machine)
4. Orchestration Layer (Agent)
5. Interface Layer (CLI / API)

---

# 4. Data Layer

## 4.1 Knowledge Storage

- Vector DB: Chroma (initial)
- Format: JSON + embeddings

### Schema

{
  "id": "uuid",
  "type": "signal | rule | case | risk",
  "content": "...",
  "tags": ["gold", "macro"],
  "confidence": 0.8,
  "source": "tutorial_x"
}

---

## 4.2 Market Data Input

Sources:
- Price feeds
- Macro indicators
- News APIs

Normalized format:

{
  "timestamp": "...",
  "asset": "gold",
  "price": 2030,
  "volatility": 0.12,
  "macro": {
    "liquidity": "tight",
    "risk": "high"
  }
}

---

# 5. Skill Engine (Core)

## 5.1 Design

- Declarative rule engine
- YAML-based
- Stateless execution

## 5.2 Skill Schema

name: gold_breakout_signal
inputs:
  - price
  - resistance
  - macro_risk

logic:
  - condition: price > resistance
    output: strong_buy
    score: 0.9
  - condition: macro_risk == high
    modifier: -0.2

---

## 5.3 Execution Model

1. Parse inputs
2. Evaluate conditions
3. Generate signal
4. Apply modifiers
5. Output structured result

---

# 6. Decision Engine

## 6.1 Signal Format

{
  "signal": "strong_buy",
  "score": 0.85,
  "confidence": 0.7,
  "source": "gold_breakout_signal"
}

---

## 6.2 Aggregation

final_score = Σ(signal_score × weight)

---

## 6.3 Conflict Resolution

Rules:

- Liquidity constraint overrides risk-on signals
- Macro > technical
- Extreme risk → force neutral

---

# 7. State Machine

## 7.1 States

- RISK_ON
- RISK_OFF
- PANIC
- TRANSITION

## 7.2 Transitions

Driven by:
- Macro signals
- Volatility thresholds

---

# 8. Orchestration (Agent)

## 8.1 Flow

1. Fetch data
2. Retrieve knowledge (RAG)
3. Execute skills
4. Aggregate signals
5. Apply state machine
6. Output decision

---

## 8.2 Output Format

{
  "state": "RISK_OFF",
  "decision": "REDUCE_POSITION",
  "confidence": 0.76,
  "explanation": [
    "liquidity tight",
    "macro risk high"
  ]
}

---

# 9. Extensibility

- Add new skills without modifying core
- Plug new data sources
- Strategy versioning
- Multi-asset support

---

# 10. Stability & Reliability

- All logic deterministic
- No direct LLM decision authority
- Fallback rules
- Logging required for every step

---

# 11. Observability

Logs must include:
- Input data snapshot
- Skill outputs
- Score breakdown
- Final decision path

---

# 12. Suggested Tech Stack

- Python
- FastAPI (API layer)
- Chroma (vector DB)
- Redis (cache)
- YAML (strategy config)

---

# 13. Minimal MVP Scope

- Single asset (gold or BTC)
- 5–10 skills
- Manual data input
- CLI output

---

# 14. Future Enhancements

- Backtesting engine
- Reinforcement learning optimization
- Multi-agent collaboration
- Portfolio allocation module

---

# END
