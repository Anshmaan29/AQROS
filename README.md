# AQROS — Autonomous Quant Research Operating System

An AI-native quantitative investment platform designed like an institutional hedge fund would build it today: multi-agent reasoning, rigorous point-in-time-correct data, honest validation, and a sovereign risk layer — with autonomy earned only through extensive validation.

> **This is not a trading bot.** It is a complete, scientific operating system for quantitative research, autonomous financial reasoning, model development, risk management, and continuous self-improvement.

## What's in this repo

| Path | What it is |
|---|---|
| **`CLAUDE.md`** | Permanent instructions any AI assistant reads before working here (rules, standards, architecture). |
| **`steps_to_Create.md`** | Beginner-friendly, step-by-step guide to actually build the project using free/cheap tools. |
| **`docs/`** | The full institutional-grade design (five documents — see below). |

## The design documents (`docs/`)

| Document | Role | Covers |
|---|---|---|
| `about.md` | The pitch | Plain-language project vision |
| `AI_QUANT_PLATFORM_BLUEPRINT.md` | The body | Distributed system, microservices, event backbone, failure/scaling/security |
| `claude_aiBrain.md` | The mind | Cognitive architecture, 18–20 AI agents, memory, consensus, confidence, regimes |
| `claude_ROI.md` | The foundation | Knowledge & data layer, point-in-time correctness, feature store, ontologies |
| `claude_MLResearchFramework.md` | The discipline | ML models, validation, overfitting prevention, drift, the research pipeline |
| `Execution_Blueprint.md` | The build plan | Repo layout, roadmap, MVP, priorities, testing, CI/CD, infrastructure |

## How it's built — in stages (trust-gated)

```
MVP  → Research only. Read data, predict, backtest honestly. No trading.
V1   → Paper trading (fake money, live prices) → tiny supervised real capital.
V2   → The multi-agent AI brain: agents debate, a risk kernel governs, it self-improves.
Future → More markets (crypto, forex, options), bigger scale, more advanced AI.
```

Capital-at-risk grows **only** as validated reliability grows. Research and paper stages are always safe (no real money).

## Core principles

- **Point-in-time correctness** — the system can never "cheat" by seeing future data.
- **One codebase for backtest, paper, and live** — the trading logic is never duplicated.
- **The AI proposes; the risk kernel disposes** — hard, human-owned safety limits the AI can never raise.
- **Everything is reproducible, versioned, and explainable** — every decision can justify itself.
- **Assume every impressive backtest is a bug until proven otherwise** — overfitting and data leakage are the enemy.

## Getting started

New here? Open **`steps_to_Create.md`** and follow Part 10 ("What To Do Right Now"). It walks you from an empty machine to a working research MVP using free tools.

Building with an AI assistant? Point it at **`CLAUDE.md`** first — it contains the standing rules and conventions for this repository.

---

*Status: architecture designed; implementation follows the roadmap in `docs/Execution_Blueprint.md`.*
