# MIKE-1

**Market Intelligence & Knowledge Engine**

A personal trading discipline system that removes emotion from execution.

## Philosophy

MIKE-1 is YOU — codified. It doesn't predict markets. It enforces YOUR discipline, YOUR patterns, YOUR rules.

- Watches when you can't
- Acts when you'd hesitate
- Trims when you'd hold
- Stops when you'd hope

## Architecture

```
MIKE-1/
├── engine/          # Python - Core trading logic (runs locally)
├── api/             # Node/TS - Read-only API (Vercel)
├── ui/              # Dashboard (Vercel)
├── config/          # Versioned configuration files
└── db/              # Database schemas and migrations
```

## The Three Minds

1. **Scout** - Watches your basket, detects catalysts
2. **Judge** - Grades setups: A / B / No Trade
3. **Executor** - Sizes, buys, monitors, trims, stops

## Core Principles

- Execution > Intelligence
- Fewer trades > More trades
- Capital preservation > Optimization
- Rules are superior to feelings
- Data must reflect reality, not ego

## Tech Stack

- **Engine**: Python (local machine)
- **API/UI**: Vercel
- **Database**: NeonDB (Postgres)
- **Broker**: Robinhood via robin_stocks

## Build Phases

- [x] Phase 0: Project structure
- [ ] Phase 1: Executor + Risk Governor
- [ ] Phase 2: Judge automation
- [ ] Phase 3: Scout automation
- [ ] Phase 4: Analytics + learning loop
