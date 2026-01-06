# MIKE-1 Context for Claude

**Last Updated:** 2026-01-05 (Judge module fully tested with direction-aware scoring)

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
├── engine/
│   └── src/mike1/
│       ├── core/
│       │   ├── config.py         # Pydantic config loader (includes AtrTrailingConfig)
│       │   ├── position.py       # Position tracking, HWM, ATR stop logic
│       │   ├── risk_governor.py  # Absolute authority on risk
│       │   └── trade.py          # Trade grading (A/B/NO_TRADE)
│       └── modules/
│           ├── broker.py         # Base Broker ABC + PaperBroker (includes get_atr)
│           ├── broker_alpaca.py  # Alpaca integration (includes get_atr)
│           ├── broker_factory.py # Creates brokers with failover
│           ├── executor.py       # Exit enforcement (ATR trailing at line 256)
│           └── logger.py         # Database logging
├── .env                  # Secrets (Alpaca keys, DATABASE_URL)
└── CLAUDE_CONTEXT.md     # This file
```

## Three Minds Architecture

1. **Scout** - Detects opportunities (NOT BUILT YET)
2. **Judge** - Scores and grades trades (FULLY WORKING + TESTED)
3. **Executor** - Enforces exits without emotion (FULLY WORKING)

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
- **Why:** Prevents auto-exercise (you'd get 100 shares) or letting ITM options expire worthless
- **Config:** `exits.force_close_0dte_time: "15:30"`

**Why different strategies?**
- Multi-contracts: Scale out, lock profits at milestones
- Single contracts: Can't scale out, so protect gains at ANY level

### ATR Trailing Stop (Single Contracts)

**Config:** `exits.atr_trailing` in default.yaml
```yaml
atr_trailing:
  enabled: true        # Use ATR trailing for single contracts
  multiplier: 2.5      # 2.5 = 25% trailing stop from HWM
  period: 14           # ATR lookback (for future use)
```

**Simple Formula:** `multiplier * 10 = trailing stop percentage`
- multiplier 2.0 = 20% trailing stop
- multiplier 2.5 = 25% trailing stop

**Example:**
- Entry: $1.00
- Rises to HWM: $1.33 (+33%)
- 25% trail from HWM: stop at $1.00
- If price drops to $1.00, EXIT (breakeven)

**Code Flow:**
1. Position created → `executor._track_new_position()` sets `atr_stop_active=True` for single contracts
2. Every 30s poll → `executor._evaluate_position()` checks `pos.should_atr_trailing_stop()`
3. If `drawdown_from_high >= 20%` → `executor._execute_atr_trailing_stop()`

**Key Files:**
- [position.py:167-199](engine/src/mike1/core/position.py#L167-L199) - `should_atr_trailing_stop()`, `atr_stop_level` property
- [executor.py:121-138](engine/src/mike1/modules/executor.py#L121-L138) - ATR setup on position creation
- [executor.py:256-293](engine/src/mike1/modules/executor.py#L256-L293) - `_execute_atr_trailing_stop()`
- [broker_alpaca.py:514-581](engine/src/mike1/modules/broker_alpaca.py#L514-L581) - `get_atr()` calculation

### Option Selection
- A-tier delta: 0.30-0.45
- B-tier delta: 0.15-0.30
- DTE range: 3-14 days
- Min open interest: 500
- Max bid-ask spread: 10%

### Scoring Criteria (5 points for A-tier, 3 for B-tier)
- Catalyst recency (2 pts): News within 4 hours
- Price confirms (1 pt): Above/below VWAP
- Volume spike (1 pt): >2x average
- Trend aligned (1 pt): Moving with broader trend
- Not overextended (1 pt): RSI not extreme

## Exit Priority Order

The executor checks exits in this order (first match wins):

1. **Hard Stop (-50%)** - Always checked first, non-negotiable
2. **0DTE Force Close (3:30 PM ET)** - Closes 0DTE positions before Alpaca cutoff to capture intrinsic value
3. **DTE Force Close** - If DTE <= close_at_dte config
4. **ATR Trailing Stop** - Single contracts only, trails from entry
5. **Percentage Trailing Stop** - Multi-contracts, after trim 1
6. **Trim 2 (+50%)** - Multi-contracts only
7. **Trim 1 (+25%)** - Multi-contracts sell 50%, single contracts just activate trailing

## Current Status

### Working (Tested Live on Paper)
- [x] Config loading from YAML
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
- [x] **ATR trailing stops** - 20% from HWM, single contracts only
- [x] **Single contract handling** - ATR trail from entry, no trims
- [x] **get_atr()** - Calculates ATR from Alpaca historical bars

### Not Built Yet
- [ ] Scout module (signal detection)
- [ ] Option chain scanning (find right strike/expiration)
- [ ] CLI commands for status/arm/kill (mike1 status, arm, positions)
- [ ] Re-entry logic

## Judge Module (FULLY WORKING)

The Judge grades trade candidates on a weighted scoring system. It provides objective assessment but does NOT decide whether to trade.

**Usage:**
```bash
cd engine
python judge_ticker.py NVDA call
python judge_ticker.py SPY put --strike 580 --expiration 2026-01-10
python judge_ticker.py TSLA call --no-llm  # Skip LLM scoring
```

**Scoring Factors (weighted):**
- Technical (35%): Volume spike, VWAP alignment, RSI
- Liquidity (35%): Open interest, bid-ask spread, unusual options activity (UOA)
- Catalyst (30%): LLM-assessed news/sentiment + social data (requires GEMINI_API_KEY)

**Social Data Sources:**
- StockTwits: Sentiment, message volume, trending status
- Reddit: r/wallstreetbets, r/options, r/stocks mentions
- Alpha Vantage: News sentiment scores (requires ALPHAVANTAGE_API_KEY)

**Unusual Options Activity (UOA) Detection:**
- OI Spike: Open interest >3x average
- Volume Spike: Option volume >5x OI
- Sweep Detection: Large, fast orders across exchanges
- Premium Tracking: Big money flow direction

**Direction-Aware Technical Scoring:**
The technical score adjusts based on whether you're trading a CALL or PUT:
- **CALL + Price above VWAP** = +3 points (bullish confirmation)
- **CALL + Price below VWAP** = -2 points (fighting the trend)
- **PUT + Price below VWAP** = +3 points (bearish confirmation)
- **PUT + Price above VWAP** = -2 points (fighting the trend)

This ensures the system catches both bull AND bear setups correctly.

**Output:**
- Grade: A-TIER (>=7.0), B-TIER (5.0-6.9), NO-TRADE (<5.0)
- Score: 0-10
- Reasoning for each factor

**Key Files:**
- [judge.py](engine/src/mike1/modules/judge.py) - Core Judge logic (VWAP alignment at lines 521-535)
- [llm_client.py](engine/src/mike1/modules/llm_client.py) - Gemini integration
- [social.py](engine/src/mike1/modules/social.py) - Social data aggregation
- [judge_ticker.py](engine/judge_ticker.py) - CLI tool
- [broker_alpaca.py:620-852](engine/src/mike1/modules/broker_alpaca.py#L620-L852) - Data methods (volume, VWAP, RSI, news)

## Live Trading Session Results (2026-01-05)

**Trades Executed:**
| Ticker | Strike | Type | Entry | Exit | P&L | Exit Reason |
|--------|--------|------|-------|------|-----|-------------|
| SPY | $687 | Put | $0.40 | $0.10 | -$30 | Hard stop (-75%) |
| QQQ | $618 | Put | $0.67 | $0.33 | -$34 | Hard stop (-50.7%) |
| QQQ | $617 | Put | $0.32 | $0.15 | -$17 | Hard stop (-53.1%) |
| MSTR | $177.50 | Call | $0.94 | $1.41 | +$47 | Trim 2 at +50% |
| SPY | $687 | Put | $1.05 | $1.04 | -$1 | Held at close (1DTE) |

**Net P&L:** -$35 (paper)

**Key Observations:**
1. Hard stops work but can overshoot on 0DTE due to 30s polling
2. MSTR hit +50% and exited (before ATR trailing was implemented)
3. SPY 1DTE hit +22% HWM, faded to -1% at close - **would have saved ~$4 with ATR trailing**

## Credentials Location

All in `.env` (gitignored):
- `ALPACA_API_KEY` - Paper trading key
- `ALPACA_SECRET_KEY` - Paper trading secret
- `DATABASE_URL` - NeonDB connection string
- `ALPACA_PAPER=true` - Use paper trading

## Key Files to Read First

1. `config/default.yaml` - All trading rules (see `atr_trailing` section)
2. `engine/src/mike1/core/risk_governor.py` - Risk enforcement
3. `engine/src/mike1/core/position.py` - Position tracking (ATR stop methods at line 167)
4. `engine/src/mike1/modules/executor.py` - Exit logic (ATR trailing at line 256)
5. `engine/src/mike1/modules/broker_alpaca.py` - Alpaca integration (get_atr at line 514)
6. `engine/src/mike1/modules/judge.py` - Trade scoring and grading (direction-aware at lines 521-535)
7. `engine/src/mike1/modules/social.py` - Social data aggregation (StockTwits, Reddit, Alpha Vantage)
8. `engine/test_judge_integration.py` - Judge test patterns (mock setup examples)

## Test Commands

```bash
cd c:/Users/mccar/MIKE-1/engine

# Test Alpaca connection
python test_alpaca_connection.py

# Initialize database
python init_database.py

# Test full engine pipeline
python test_engine.py

# Test Gemini LLM response parsing (JSON, markdown, edge cases)
python test_gemini_parsing.py

# Test Judge integration (grade thresholds, direction scoring, UOA, mocked APIs)
python test_judge_integration.py

# START THE ENGINE (dry run - monitors but doesn't trade)
python run_mike1.py --dry-run

# START THE ENGINE (live - will execute trades!)
python run_mike1.py --live
# Requires typing 'CONFIRM' to proceed

# Use local paper broker instead of Alpaca
python run_mike1.py --paper-broker
```

**Test Coverage:**
- `test_gemini_parsing.py` (16 tests): JSON parsing, markdown code blocks, score conversion, edge cases
- `test_judge_integration.py` (18 tests): Grade thresholds (A/B/NO_TRADE), direction-aware scoring (CALL vs PUT), catalyst scoring with confidence levels, unusual options activity detection, social client mocking

## Important Notes

- System uses `armed: false` by default - must be armed to execute real trades
- Paper trading is the default (`ALPACA_PAPER=true`)
- All numbers come from config, never hardcoded
- Risk Governor has absolute authority - cannot be bypassed
- Position class uses `ticker` not `symbol`, `contracts` not `quantity`
- OptionType is an enum: `OptionType.CALL` / `OptionType.PUT`
- **30s polling interval** - can cause stop slippage on fast-moving 0DTE options
- **Single contracts use ATR trailing (20% from HWM)** - protects gains at any level
- **Multi-contracts use trim targets** - scale out at +25% and +50%

## Ticker Universe (from config)

**Tech:** NVDA, AMD, SMCI, PLTR, META, GOOGL, MSFT, AAPL
**Biotech:** LLY, MRNA, NVO
**Momentum:** TSLA, MSTR, GME, COIN
**ETFs:** SPY, QQQ, IWM

## User Preferences

- User is building this to enforce his own trading discipline
- Focus on options, specifically calls and puts on momentum stocks
- Prefers simple, working code over complex abstractions
- Values the system preventing emotional trading decisions
- **Quant mindset:** Cut losers fast, let winners run - positive skew from occasional big winners
- **0DTE/1DTE options** - Accepts some stop slippage as tradeoff for liquidity

## Lessons Learned

1. **Stop slippage is expected** - With 30s polling and 0DTE options, hitting -53% when target is -50% is normal
2. **ATR trailing protects gains early** - No need to wait for +25% activation threshold
3. **Simple formulas win** - `multiplier * 10 = stop %` is easy to reason about and tune
4. **Hard stop is non-negotiable** - Prevents catastrophic losses even if it triggers beyond target
5. **Different strategies for different position sizes** - Single contracts can't trim, so they need different exit logic
6. **Direction-aware scoring catches bear cases** - PUTs need price below VWAP, CALLs need price above VWAP
7. **Mock social clients in tests** - Use `@patch('mike1.modules.social.get_social_client')` to prevent real API calls
8. **Grade thresholds are strict** - A-TIER (>=7.0), B-TIER (5.0-6.9), NO_TRADE (<5.0) - no rounding
