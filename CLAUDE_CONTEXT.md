# MIKE-1 Context for Claude

**Last Updated:** 2026-01-06 (Full pipeline integration complete)

**GitHub:** https://github.com/Born14/MIKE-1

## What is MIKE-1?

MIKE-1 (Market Intelligence & Knowledge Engine) is a personal options trading system that removes emotion from trade execution. It enforces the user's pre-defined rules automatically.

**Philosophy:** "I am not my own broker. I am the strategist who sets the rules. MIKE-1 is the executor who follows them."

## Architecture

```
MIKE-1/
├── config/
│   └── default.yaml      # ALL trading rules live here (no hardcoded values)
├── db/
│   └── schema.sql        # NeonDB PostgreSQL schema
├── data/
│   ├── manual_tickers.txt  # Manual ticker input for Scout
│   └── README.md           # Usage docs for manual screening
├── engine/
│   ├── run_full_pipeline.py      # Full Scout → Curator → Judge → Executor
│   ├── run_scout.py              # Scout CLI (signal detection only)
│   ├── test_pipeline_with_signal.py  # E2E test with injected signal
│   └── src/mike1/
│       ├── core/
│       │   ├── config.py         # Pydantic config loader (includes BasketConfig)
│       │   ├── position.py       # Position tracking, HWM, ATR stop logic
│       │   ├── risk_governor.py  # Absolute authority on risk
│       │   ├── scouters_rubric.py # Centralized scoring (Delta, DTE, Tech, Liquidity)
│       │   └── trade.py          # Trade grading + TradeSignal + ScoutResult
│       └── modules/
│           ├── broker.py         # Base Broker ABC + PaperBroker (includes get_atr)
│           ├── broker_alpaca.py  # Alpaca integration (includes get_atr)
│           ├── broker_factory.py # Creates brokers with failover
│           ├── scout.py          # Signal detection (3 detectors)
│           ├── curator.py        # Option chain scanning + ranking
│           ├── executor.py       # Exit enforcement + grade filter
│           ├── judge.py          # Trade scoring with Delta/DTE
│           ├── llm_client.py     # Gemini LLM integration
│           ├── social.py         # Social data (StockTwits, Reddit, Alpha Vantage)
│           └── logger.py         # Database logging
├── .env                  # Secrets (Alpaca keys, DATABASE_URL)
└── CLAUDE_CONTEXT.md     # This file
```

## Four Minds Architecture

1. **Scout** - Detects opportunities (FULLY IMPLEMENTED ✅)
   - 3 detectors: VolumeDetector, NewsDetector, TechnicalDetector
   - Integrates social data (StockTwits, Reddit, Alpha Vantage)
   - LLM-powered catalyst assessment via Gemini
   - Priority-based signal ranking
   - Cooldown system prevents duplicate signals

2. **Curator** - Selects optimal option contracts from chain (FULLY IMPLEMENTED ✅)
   - Scans option chains and ranks by 100-point score
   - Filters by delta (0.15-0.45), DTE (3-14), liquidity (OI ≥500)
   - Detects unusual options activity (Vol/OI ratio)
   - Returns top 3 candidates for Judge evaluation

3. **Judge** - Scores and grades trades (FULLY WORKING + TESTED ✅)
   - Direction-aware technical scoring
   - Delta/DTE/liquidity assessment
   - LLM catalyst validation
   - A-TIER (≥7.0), B-TIER (5.0-6.9), NO_TRADE (<5.0)

4. **Executor** - Enforces exits without emotion (FULLY WORKING ✅)
   - Multi-contract trims (+25%, +50%)
   - ATR trailing stops (single contracts)
   - Hard stop (-50%), 0DTE force close
   - Grade filter (A-TIER only mode)

## Grade Filter (A-TIER Only Mode)

**Current Setting:** `scoring.min_trade_grade: "A"` - Only A-TIER trades execute

The Executor checks grade before execution:
- **A-TIER (≥7.0):** ✅ Executes
- **B-TIER (5.0-6.9):** ❌ Blocked until validated
- **NO_TRADE (<5.0):** ❌ Always blocked

To relax to A+B tier (after validation):
```yaml
scoring:
  min_trade_grade: "B"  # Now accepts A + B-TIER
```

**Code:** [executor.py:494-505](engine/src/mike1/modules/executor.py#L494-L505)

## Scoring System

### Centralized Rubric (scouters_rubric.py)

**Delta Scoring:**
| Range | Grade | Points |
|-------|-------|--------|
| 0.30-0.45 | A-tier | +2 |
| 0.15-0.30 | B-tier | +1 |
| <0.15 | Lottery | -1 |

**DTE Scoring:**
| Range | Assessment | Points |
|-------|------------|--------|
| <2 days | Gamma trap | -2 |
| 3-14 days | Sweet spot | +1 |
| >14 days | Neutral | 0 |

**Technical Scoring (direction-aware):**
- Volume spike (3x+ avg): +4
- VWAP alignment: +3/-2
- RSI reversal setup: +2/-3

**Liquidity Scoring:**
- OI ≥1000: +4
- Tight spread ≤5%: +4
- Unusual activity: +1.5

### Weighted Final Score
- Technical: 35%
- Liquidity: 35% (includes Delta/DTE)
- Catalyst: 30%

## Trading Rules (from config/default.yaml)

### Risk Limits
- Max risk per trade: $200
- Max contracts: 1
- Max trades per day: 2
- Daily loss limit: $100 (triggers lockout)
- Kill switch: Manual emergency stop

### Exit Rules (Non-Negotiable)

#### Multi-Contract Positions (2+ contracts):
- **Trim 1:** +25% → Sell 50%
- **Trim 2:** +50% → Sell remaining
- **Trailing Stop:** 25% from high water mark (only after Trim 1)
- **Hard Stop:** -50% (non-negotiable)
- **DTE Close:** Force exit at 1 DTE

#### Single Contract Positions (ATR-Based Trailing):
- **Trail from entry:** 25% trailing stop from HWM (no activation threshold)
- **No trims:** Can't sell half of 1 contract
- **Exit:** When price drops 25% from high water mark
- **Hard Stop:** -50% still applies (checked first)

#### 0DTE Protection:
- **Force close at 3:30 PM ET** - Sells any 0DTE position before Alpaca's cutoff
- **Config:** `exits.force_close_0dte_time: "15:30"`

### Option Selection
- A-tier delta: 0.30-0.45
- B-tier delta: 0.15-0.30
- DTE range: 3-14 days
- Min open interest: 500
- Max bid-ask spread: 10%

## Exit Priority Order

1. **Hard Stop (-50%)** - Always checked first, non-negotiable
2. **0DTE Force Close (3:30 PM ET)** - Closes 0DTE positions before Alpaca cutoff
3. **DTE Force Close** - If DTE <= close_at_dte config
4. **ATR Trailing Stop** - Single contracts only, trails from entry
5. **Percentage Trailing Stop** - Multi-contracts, after trim 1
6. **Trim 2 (+50%)** - Multi-contracts only
7. **Trim 1 (+25%)** - Multi-contracts sell 50%, single contracts just activate trailing

## Current Status

### Working (Tested ✅)
- [x] Config loading from YAML (multi-source ticker basket)
- [x] Risk Governor (limits, kill switch, daily tracking)
- [x] Position tracking with high water mark
- [x] Exit trigger detection (trims, stops)
- [x] Alpaca broker connection (paper trading)
- [x] Paper broker for testing
- [x] NeonDB schema + connection
- [x] **Executor with trade execution** (broker.sell_option wired up)
- [x] **Position monitoring loop** (30s polling)
- [x] **CLI to start engine** (run_mike1.py)
- [x] **Hard stops** - Tested, working
- [x] **ATR trailing stops** - 25% from HWM, single contracts only
- [x] **get_atr()** - Calculates ATR from Alpaca historical bars
- [x] **Judge with Delta/DTE scoring** - Centralized rubric
- [x] **Grade filter** - A-TIER only until validated
- [x] **GitHub versioning** - https://github.com/Born14/MIKE-1
- [x] **Scout module** - 3 detectors (Volume, News, Technical)
- [x] **Curator module** - Option chain scanning + 100pt ranking
- [x] **Full pipeline integration** - Scout → Curator → Judge → Executor
- [x] **Manual ticker input** - Phase 1 (data/manual_tickers.txt)
- [x] **LLM integration** - Gemini for catalyst assessment
- [x] **Social data** - StockTwits, Reddit, Alpha Vantage

### Not Built Yet
- [ ] CLI commands for status/arm/kill (mike1 status, arm, positions)
- [ ] Re-entry logic
- [ ] Automated screener (Phase 2/3 - market-wide scanning)
- [ ] Real-time data feed (currently 30s polling)

## Scout Module (FULLY IMPLEMENTED)

**Purpose:** Detect trading signals from market data and social sentiment

**3 Detectors:**
1. **VolumeDetector** (Priority 5)
   - Volume ≥ 2.5x average
   - Absolute volume > 1M shares
   - Clear direction (price vs VWAP)

2. **NewsDetector** (Priority 8 - highest)
   - Social mentions ≥10 (StockTwits, Reddit, Alpha Vantage)
   - LLM-powered catalyst assessment (Gemini)
   - Sentiment-based direction

3. **TechnicalDetector** (Priority 4)
   - RSI extremes (<30 oversold, >70 overbought)
   - VWAP context

**Usage:**
```bash
cd engine
python run_scout.py                    # Scan all tickers
python run_scout.py --clear-cooldowns  # Reset cooldowns
```

**Ticker Sources (Priority Order):**
1. Manual input - data/manual_tickers.txt (Phase 1)
2. Core watchlist - SPY, QQQ, NVDA, TSLA
3. Categories - Tech, Biotech, Momentum, ETFs
4. Screener - Not yet implemented (Phase 2/3)

**Key Files:**
- [scout.py](engine/src/mike1/modules/scout.py) - Core Scout logic + 3 detectors
- [social.py](engine/src/mike1/modules/social.py) - Social data aggregation
- [llm_client.py](engine/src/mike1/modules/llm_client.py) - Gemini integration

## Curator Module (FULLY IMPLEMENTED)

**Purpose:** Find and rank best option contracts for a signal

**100-Point Scoring System:**
- Delta scoring: 30 points (0.30-0.45 = A-tier)
- Liquidity: 30 points (OI, spread, volume)
- Unusual activity: 20 points (Vol/OI ratio)
- ATM proximity: 20 points

**Filters:**
- Delta: 0.15-0.45
- DTE: 3-14 days
- Open Interest: ≥500
- Bid-Ask spread: ≤10%

**Usage:**
```bash
cd engine
python curator_judge.py NVDA call  # Find + grade best options
```

**Key Files:**
- [curator.py](engine/src/mike1/modules/curator.py) - Option chain scanning + ranking

## Judge Module (FULLY WORKING)

**Usage:**
```bash
cd engine
python judge_ticker.py NVDA call
python judge_ticker.py SPY put --strike 580 --expiration 2026-01-10
python judge_ticker.py TSLA call --no-llm  # Skip LLM scoring
```

**Output:**
- Grade: A-TIER (>=7.0), B-TIER (5.0-6.9), NO-TRADE (<5.0)
- Score: 0-10
- Reasoning for each factor

**Key Files:**
- [judge.py](engine/src/mike1/modules/judge.py) - Core Judge logic
- [scouters_rubric.py](engine/src/mike1/core/scouters_rubric.py) - Centralized scoring
- [llm_client.py](engine/src/mike1/modules/llm_client.py) - Gemini integration
- [social.py](engine/src/mike1/modules/social.py) - Social data aggregation

## Full Pipeline Integration

**Usage:**
```bash
cd engine
python run_full_pipeline.py                  # Dry-run (simulation)
python run_full_pipeline.py --live           # Live mode (if armed)
python run_full_pipeline.py --max-signals 3  # Limit processing
python run_full_pipeline.py --clear-cooldowns # Reset Scout cooldowns
```

**Flow:**
1. Scout scans all tickers → detects signals
2. Curator finds best 3 option contracts per signal
3. Judge scores and grades each candidate
4. Executor manages approved A-tier trades

**Key Files:**
- [run_full_pipeline.py](engine/run_full_pipeline.py) - Complete integration
- [test_pipeline_with_signal.py](engine/test_pipeline_with_signal.py) - E2E test

## Credentials Location

All in `.env` (gitignored):
- `ALPACA_API_KEY` - Paper trading key
- `ALPACA_SECRET_KEY` - Paper trading secret
- `DATABASE_URL` - NeonDB connection string
- `ALPACA_PAPER=true` - Use paper trading
- `GEMINI_API_KEY` - For LLM catalyst scoring

## Key Files to Read First

1. `config/default.yaml` - All trading rules
2. `engine/src/mike1/core/scouters_rubric.py` - Centralized scoring rubric
3. `engine/src/mike1/core/risk_governor.py` - Risk enforcement
4. `engine/src/mike1/modules/executor.py` - Exit logic + grade filter
5. `engine/src/mike1/modules/judge.py` - Trade scoring

## Test Commands

```bash
cd engine

# Test Alpaca connection
python test_alpaca_connection.py

# Initialize database
python init_database.py

# Test full engine pipeline
python test_engine.py

# Test Gemini LLM response parsing
python test_gemini_parsing.py

# Test Judge integration (grade thresholds, direction scoring, UOA)
python test_judge_integration.py

# Run Scout (signal detection only)
python run_scout.py
python run_scout.py --clear-cooldowns

# Run full pipeline (Scout → Curator → Judge → Executor)
python run_full_pipeline.py                  # Dry-run
python run_full_pipeline.py --live           # Live mode
python run_full_pipeline.py --max-signals 3  # Limit signals

# Test pipeline with injected signal
python test_pipeline_with_signal.py

# START THE ENGINE (position monitoring - monitors but doesn't trade)
python run_mike1.py --dry-run

# START THE ENGINE (live - will execute trades!)
python run_mike1.py --live
```

## Important Notes

- System uses `armed: false` by default - must be armed to execute real trades
- Paper trading is the default (`ALPACA_PAPER=true`)
- All numbers come from config, never hardcoded
- Risk Governor has absolute authority - cannot be bypassed
- **A-TIER only** until we validate performance
- **30s polling interval** - can cause stop slippage on fast-moving 0DTE options

## Ticker Universe (Multi-Source Basket)

Scout scans tickers from multiple sources (priority order):

**1. Manual Input (Highest Priority)**
- File: `data/manual_tickers.txt`
- Purpose: Feed external screening results (Finviz, TradingView, etc.)
- Age limit: 24 hours (configurable)

**2. Core Watchlist**
- SPY, QQQ, NVDA, TSLA

**3. Categories**
- **Tech:** NVDA, AMD, SMCI, PLTR, META, GOOGL, MSFT, AAPL
- **Biotech:** LLY, MRNA, NVO
- **Momentum:** TSLA, MSTR, GME, COIN
- **ETFs:** SPY, QQQ, IWM

**4. Automated Screener** (Not Yet Implemented - Phase 2/3)
- Market-wide scanning
- Real-time unusual activity detection

**Total Tickers:** 19 (deduplicated across all sources)

## Lessons Learned

1. **Stop slippage is expected** - With 30s polling and 0DTE options, hitting -53% when target is -50% is normal
2. **ATR trailing protects gains early** - No need to wait for +25% activation threshold
3. **Simple formulas win** - `multiplier * 10 = stop %` is easy to reason about and tune
4. **Hard stop is non-negotiable** - Prevents catastrophic losses even if it triggers beyond target
5. **Direction-aware scoring catches bear cases** - PUTs need price below VWAP, CALLs need price above VWAP
6. **Grade thresholds are strict** - A-TIER (>=7.0), B-TIER (5.0-6.9), NO_TRADE (<5.0)
7. **Start conservative** - A-TIER only until we validate that grading correlates with P&L
8. **Delta/DTE matter** - Sweet spot is 0.30-0.45 delta, 3-14 DTE
