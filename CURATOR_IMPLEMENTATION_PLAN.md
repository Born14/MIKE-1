# Curator (4th Layer) Implementation Plan

## Executive Summary

The **Curator** is the missing 4th layer in MIKE-1's architecture. It bridges the gap between Scout's signal detection and Judge's trade evaluation by selecting optimal option contracts from the full chain.

### Current Problem
- Judge requires `strike` and `expiration` as manual inputs
- Without them, Judge can't score Delta (0.30-0.45), DTE (3-14 days), or Liquidity (OI/spread)
- Humans currently act as the Curator when running `judge_ticker.py --strike X --expiration Y`
- No path to full automation

### Solution: Curator Layer
```
Scout → CURATOR → Judge → Executor
  ↓         ↓         ↓        ↓
"NVDA   "Which   "Is this  "Execute
 call"   strike?"  A-tier?"  + manage"
```

---

## Architecture Overview

### Position in Flow
```python
# Current (manual)
scout_signal = ("NVDA", "call")  # From Scout (not built yet)
strike, expiration = HUMAN_SELECTS_MANUALLY  # ← Problem
verdict = judge.grade("NVDA", "call", strike, expiration)
if verdict.grade == "A": executor.execute(...)

# Proposed (automated)
scout_signal = ("NVDA", "call")
candidates = curator.find_best_options("NVDA", "call")  # ← NEW
verdicts = [judge.grade("NVDA", "call", c.strike, c.expiration) for c in candidates]
best = max(verdicts, key=lambda v: v.score)
if best.grade == "A": executor.execute(...)
```

### Module Structure
```
engine/src/mike1/modules/
├── curator.py           # NEW - Main Curator class
└── curator_test.py      # NEW - Tests for Curator

engine/
├── curator_ticker.py    # NEW - CLI for manual testing
└── test_curator.py      # NEW - Integration tests
```

---

## Data Models

### OptionCandidate
```python
@dataclass
class OptionCandidate:
    """
    A single option contract candidate from chain scan.
    """
    symbol: str
    strike: float
    expiration: str
    option_type: str  # "call" or "put"

    # Raw metrics (for filtering)
    delta: float
    dte: int
    open_interest: int
    volume: int
    bid: float
    ask: float
    spread_pct: float

    # Unusual activity
    vol_oi_ratio: float = 0
    is_unusual_activity: bool = False

    # Curator ranking score (0-100)
    # Higher = better candidate for Judge evaluation
    curator_score: float = 0

    # Ranking reasons
    ranking_reasons: list[str] = field(default_factory=list)
```

### CuratorResult
```python
@dataclass
class CuratorResult:
    """
    Result of Curator's chain scan.
    """
    symbol: str
    direction: str

    # Top candidates (sorted by curator_score desc)
    candidates: list[OptionCandidate]

    # Scan metadata
    total_contracts_scanned: int
    total_passing_filters: int
    scan_time_ms: float

    # Warnings
    warnings: list[str] = field(default_factory=list)
```

---

## Core Algorithm

### 1. Chain Scanning
```python
def find_best_options(
    self,
    symbol: str,
    direction: str,
    top_n: int = 3
) -> CuratorResult:
    """
    Scan option chain and return top N candidates.

    Algorithm:
    1. Get valid expiration dates (DTE 3-14)
    2. For each expiration, fetch full chain via broker.get_option_chain()
    3. Apply hard filters (min OI, max spread, delta range)
    4. Rank remaining candidates
    5. Return top N
    """
```

### 2. Filtering (Hard Constraints)

**From config/default.yaml:**
```yaml
options:
  a_tier:
    delta_min: 0.30
    delta_max: 0.45
  b_tier:
    delta_min: 0.15
    delta_max: 0.30
  min_dte: 3
  max_dte: 14
  min_open_interest: 500
  max_bid_ask_spread_pct: 0.10
```

**Filter Logic:**
```python
def _passes_filters(self, quote: OptionQuote, grade_tier: str) -> bool:
    """
    Check if option passes hard filters.

    Returns False if:
    - Delta outside target range
    - DTE outside range
    - OI below minimum
    - Spread too wide
    """
```

### 3. Ranking (Soft Scoring)

**Curator Score = 0-100 points**

| Factor | Weight | Logic |
|--------|--------|-------|
| **Delta Proximity** | 30% | Distance from ideal (0.35-0.40 for A-tier) |
| **Liquidity** | 30% | OI + tight spread |
| **Unusual Activity** | 20% | Vol/OI >1.25 = smart money |
| **ATM Proximity** | 20% | Closer to current price = better gamma |

```python
def _rank_candidate(self, quote: OptionQuote, stock_price: float) -> float:
    """
    Score 0-100. Higher = better candidate for Judge.

    This is NOT Judge scoring - it's pre-filtering to reduce
    the number of contracts Judge needs to evaluate.
    """
    score = 0
    reasons = []

    # 1. Delta Proximity (30 points)
    ideal_delta = self.config.curator.ideal_delta  # 0.375 default
    delta_distance = abs(quote.delta - ideal_delta)
    delta_score = max(0, 30 - delta_distance * 100)
    score += delta_score
    reasons.append(f"Delta {quote.delta:.2f} ({delta_score:.0f}pts)")

    # 2. Liquidity (30 points)
    oi_score = min(15, quote.open_interest / 100)
    spread_score = max(0, 15 - quote.spread_pct * 3)
    score += oi_score + spread_score
    reasons.append(f"OI {quote.open_interest:,} ({oi_score:.0f}pts)")
    reasons.append(f"Spread {quote.spread_pct:.1f}% ({spread_score:.0f}pts)")

    # 3. Unusual Activity (20 points)
    if quote.vol_oi_ratio >= 1.25:
        score += 20
        reasons.append(f"UNUSUAL: {quote.vol_oi_ratio:.1f}x (+20pts)")

    # 4. ATM Proximity (20 points)
    moneyness = quote.strike / stock_price
    atm_distance = abs(moneyness - 1.0)
    atm_score = max(0, 20 - atm_distance * 50)
    score += atm_score
    reasons.append(f"Moneyness {moneyness:.2f} ({atm_score:.0f}pts)")

    return score, reasons
```

---

## Config Changes

### New Section: `curator`
```yaml
# config/default.yaml

# =============================================================================
# CURATOR (Chain Selection)
# =============================================================================
curator:
  # How many candidates to pass to Judge
  max_candidates: 3

  # Ideal delta target (A-tier midpoint)
  ideal_delta: 0.375  # Midpoint of 0.30-0.45 range

  # Prefer strikes near ATM
  prefer_atm: true

  # Boost unusual activity in ranking
  unusual_activity_boost: 20  # Points added if Vol/OI >= 1.25

  # Expiration date handling
  expiration_strategy: "calculate"  # "calculate" | "fetch"
  # - calculate: Use next N Fridays (for weekly options)
  # - fetch: Use broker.get_expirations() if available

  # Cache chain data (reduce API calls)
  cache_chain_seconds: 60
```

---

## Implementation Steps

### Phase 1: Core Curator Module
**Files:** `engine/src/mike1/modules/curator.py`

1. **Create data models**
   - `OptionCandidate`
   - `CuratorResult`

2. **Implement `Curator` class**
   - `__init__(broker, config)`
   - `find_best_options(symbol, direction, top_n=3) -> CuratorResult`
   - `_get_valid_expirations(min_dte, max_dte) -> list[str]`
   - `_scan_expiration(symbol, expiration, direction) -> list[OptionQuote]`
   - `_passes_filters(quote, grade_tier) -> bool`
   - `_rank_candidate(quote, stock_price) -> tuple[float, list[str]]`

3. **Add caching**
   - Use `functools.lru_cache` or simple dict cache
   - Cache key: `(symbol, expiration, direction, timestamp//60)`
   - Reduces Alpaca API calls during testing

### Phase 2: Config Integration
**Files:** `config/default.yaml`, `engine/src/mike1/core/config.py`

1. **Add curator section to YAML**
   - All settings from "Config Changes" above

2. **Update Pydantic config model**
   ```python
   class CuratorConfig(BaseModel):
       max_candidates: int = 3
       ideal_delta: float = 0.375
       prefer_atm: bool = True
       unusual_activity_boost: float = 20
       expiration_strategy: str = "calculate"
       cache_chain_seconds: int = 60
   ```

3. **Add to main Config**
   ```python
   class Config(BaseModel):
       # ... existing fields ...
       curator: CuratorConfig = Field(default_factory=CuratorConfig)
   ```

### Phase 3: Expiration Date Handling
**Files:** `engine/src/mike1/modules/curator.py`, `engine/src/mike1/utils/dates.py` (NEW)

1. **Create `dates.py` utility**
   ```python
   def get_next_fridays(count: int) -> list[str]:
       """Get next N Fridays (standard options expiration)."""

   def calculate_dte(expiration: str) -> int:
       """Calculate days to expiration."""

   def filter_expirations_by_dte(expirations: list[str], min_dte: int, max_dte: int) -> list[str]:
       """Filter expiration dates by DTE range."""
   ```

2. **Implement in Curator**
   ```python
   def _get_valid_expirations(self) -> list[str]:
       """
       Get valid expiration dates within DTE range.

       Strategy:
       - Calculate next 4 Fridays (covers up to ~28 DTE)
       - Filter to min_dte:max_dte range (3-14 days)
       - Return list of YYYY-MM-DD strings
       """
       if self.config.curator.expiration_strategy == "calculate":
           all_fridays = get_next_fridays(count=4)
           return filter_expirations_by_dte(
               all_fridays,
               self.config.options.min_dte,
               self.config.options.max_dte
           )
       else:
           # Future: fetch from broker API if available
           raise NotImplementedError("fetch strategy not yet implemented")
   ```

### Phase 4: CLI Tool for Testing
**Files:** `engine/curator_ticker.py`

```python
#!/usr/bin/env python
"""
Curator CLI - Find best option contracts for a ticker.

Usage:
    python curator_ticker.py NVDA call
    python curator_ticker.py SPY put --top 5
    python curator_ticker.py TSLA call --tier A
"""

import argparse
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from dotenv import load_dotenv
load_dotenv()

from mike1.modules.broker_factory import BrokerFactory
from mike1.modules.curator import Curator

def main():
    parser = argparse.ArgumentParser(description="Find best option contracts")
    parser.add_argument("symbol", help="Ticker symbol (e.g., NVDA)")
    parser.add_argument("direction", choices=["call", "put"], help="Trade direction")
    parser.add_argument("--top", type=int, default=3, help="Number of candidates to return")
    parser.add_argument("--tier", choices=["A", "B"], default="A", help="Grade tier to target")
    args = parser.parse_args()

    print(f"\n{'='*60}")
    print(f"MIKE-1 CURATOR - Finding best {args.direction}s for {args.symbol}")
    print(f"{'='*60}\n")

    # Connect to broker
    broker = BrokerFactory.create("alpaca")
    if not broker.connect():
        print("ERROR: Failed to connect to broker")
        sys.exit(1)

    # Create Curator
    curator = Curator(broker)

    # Find best options
    result = curator.find_best_options(
        symbol=args.symbol,
        direction=args.direction,
        top_n=args.top,
        grade_tier=args.tier
    )

    # Print results
    print(f"Scanned {result.total_contracts_scanned} contracts")
    print(f"Found {result.total_passing_filters} passing filters")
    print(f"Scan time: {result.scan_time_ms:.0f}ms\n")

    if result.warnings:
        print("⚠️  Warnings:")
        for warning in result.warnings:
            print(f"  - {warning}")
        print()

    print(f"🎯 Top {len(result.candidates)} Candidates:\n")
    for i, candidate in enumerate(result.candidates, 1):
        print(f"{i}. {candidate.symbol} {candidate.strike:.2f} {candidate.option_type} @ {candidate.expiration}")
        print(f"   Delta: {candidate.delta:.2f} | DTE: {candidate.dte} days | OI: {candidate.open_interest:,}")
        print(f"   Spread: {candidate.spread_pct:.1f}% | Vol/OI: {candidate.vol_oi_ratio:.2f}x")
        if candidate.is_unusual_activity:
            print(f"   🔥 UNUSUAL ACTIVITY DETECTED")
        print(f"   Curator Score: {candidate.curator_score:.0f}/100")
        print(f"   Reasons: {', '.join(candidate.ranking_reasons)}")
        print()

    return 0

if __name__ == "__main__":
    sys.exit(main())
```

### Phase 5: Integration with Judge
**Files:** `engine/curator_judge.py` (NEW)

**Combined workflow script:**
```python
#!/usr/bin/env python
"""
Full Curator → Judge pipeline.

Usage:
    python curator_judge.py NVDA call
    python curator_judge.py SPY put --verbose
"""

def main():
    # 1. Curator finds best options
    curator_result = curator.find_best_options(symbol, direction)

    # 2. Judge evaluates each candidate
    verdicts = []
    for candidate in curator_result.candidates:
        verdict = judge.grade(
            symbol=symbol,
            direction=direction,
            strike=candidate.strike,
            expiration=candidate.expiration
        )
        verdicts.append((candidate, verdict))

    # 3. Sort by Judge score
    verdicts.sort(key=lambda x: x[1].score, reverse=True)

    # 4. Print results
    print(f"\n🏆 Best Option (Curator + Judge):\n")
    best_candidate, best_verdict = verdicts[0]
    print(f"{best_candidate.symbol} {best_candidate.strike:.2f} {best_candidate.option_type} @ {best_candidate.expiration}")
    print(f"Grade: {best_verdict.grade.value}-TIER")
    print(f"Score: {best_verdict.score:.1f}/10")
    print(f"Curator: {best_candidate.curator_score:.0f}/100")
    print()
```

### Phase 6: Testing
**Files:** `engine/test_curator.py`, `engine/src/mike1/modules/curator_test.py`

1. **Unit tests (`curator_test.py`)**
   - Test filtering logic with mock data
   - Test ranking algorithm
   - Test expiration date calculation
   - Test caching behavior

2. **Integration tests (`test_curator.py`)**
   - Test with real Alpaca API (paper trading)
   - Test full pipeline: Curator → Judge
   - Test edge cases (no contracts pass filters, etc.)
   - Compare Curator picks vs random picks (Judge scores)

### Phase 7: Executor Integration
**Files:** `engine/src/mike1/modules/executor.py`

**Add Curator to Executor:**
```python
class Executor:
    def __init__(self, broker, judge, curator=None):  # ← Add curator param
        self.broker = broker
        self.judge = judge
        self.curator = curator  # ← NEW
        # ...

    def evaluate_signal(self, symbol: str, direction: str) -> Optional[JudgeVerdict]:
        """
        Evaluate a Scout signal using Curator + Judge.

        1. Curator finds best options
        2. Judge evaluates each
        3. Return best-graded option (A-tier only if min_trade_grade="A")
        """
        if not self.curator:
            logger.error("No Curator configured - cannot evaluate signal")
            return None

        # 1. Curator scan
        result = self.curator.find_best_options(symbol, direction, top_n=3)

        if not result.candidates:
            logger.warning("No valid options found", symbol=symbol)
            return None

        # 2. Judge each candidate
        verdicts = []
        for candidate in result.candidates:
            verdict = self.judge.grade(
                symbol=symbol,
                direction=direction,
                strike=candidate.strike,
                expiration=candidate.expiration
            )
            verdicts.append(verdict)

        # 3. Return best grade
        verdicts.sort(key=lambda v: v.score, reverse=True)
        best = verdicts[0]

        # 4. Check grade threshold
        min_grade = self.config.scoring.min_trade_grade
        if min_grade == "A" and best.grade != TradeGrade.A_TIER:
            logger.info("Best option below A-TIER threshold", symbol=symbol, grade=best.grade.value)
            return None

        return best
```

### Phase 8: Documentation Updates
**Files:** `CLAUDE_CONTEXT.md`, `README.md`

1. **Update architecture diagram**
   ```
   Scout → CURATOR → Judge → Executor
           ^^^^^^
           NEW 4th layer
   ```

2. **Add Curator to "What is MIKE-1?"**
   ```markdown
   ## Four Minds Architecture

   1. **Scout** - Detects opportunities (NOT BUILT YET)
   2. **Curator** - Selects best option contracts (NEW - READY TO BUILD)
   3. **Judge** - Scores and grades trades (FULLY WORKING)
   4. **Executor** - Enforces exits without emotion (FULLY WORKING)
   ```

3. **Add usage examples**
   ```bash
   # Find best option for NVDA calls
   python curator_ticker.py NVDA call

   # Full pipeline: Curator → Judge
   python curator_judge.py NVDA call
   ```

---

## Testing Strategy

### Manual Testing
```bash
# 1. Test Curator alone
python curator_ticker.py NVDA call
python curator_ticker.py SPY put --top 5

# 2. Test Curator + Judge
python curator_judge.py NVDA call

# 3. Test with different tiers
python curator_ticker.py TSLA call --tier B

# 4. Test edge cases
python curator_ticker.py GME call  # Low liquidity
python curator_ticker.py AAPL put  # High liquidity
```

### Automated Testing
```bash
cd engine
python -m pytest test_curator.py -v
python -m pytest src/mike1/modules/curator_test.py -v
```

### Validation Metrics
**Goal:** Curator's top pick should score higher with Judge than random picks

```python
# Validation script
def validate_curator():
    """
    Compare Curator picks vs random picks.

    For 10 tickers:
    1. Curator selects top 3 options
    2. Random selects 3 options from full chain
    3. Judge scores all 6
    4. Calculate: avg(curator_scores) vs avg(random_scores)

    Success: Curator avg > Random avg + 1.0 points
    """
```

---

## Dependencies

### New Python Dependencies
```bash
# No new external dependencies required!
# All functionality uses existing broker API and stdlib
```

### Internal Dependencies
- `Broker.get_option_chain()` - ✅ Already implemented
- `Judge.grade()` - ✅ Already implemented
- `ScoringRubric` - ✅ Already implemented
- Config system - ✅ Already implemented

---

## Risks & Mitigations

### Risk 1: API Rate Limits
**Problem:** Scanning multiple expirations = many API calls

**Mitigation:**
- Cache chain data (60s default)
- Limit to 2-3 expirations max (DTE 3-14 typically = 2 Fridays)
- Use `time.sleep(0.5)` between chain fetches if needed

### Risk 2: No Contracts Pass Filters
**Problem:** Illiquid tickers may have zero contracts meeting criteria

**Mitigation:**
- Return empty list with warning
- Executor handles gracefully (no trade)
- Log for analysis: "GME: 0/87 contracts passed filters"

### Risk 3: Curator vs Judge Disagreement
**Problem:** Curator ranks option #1, but Judge scores it B-tier

**Mitigation:**
- Curator is pre-filter only, Judge has final say
- Curator returns top 3, Judge picks best of those 3
- If all 3 are B-tier and min_grade="A", no trade (correct behavior)

### Risk 4: Stale Greeks
**Problem:** Broker's delta/IV data may be delayed

**Mitigation:**
- Accept stale data (all brokers have this)
- Filter is "directionally correct" even if delta is 0.38 vs 0.40
- Edge cases rare (delta won't jump from 0.35 to 0.15 in 60s)

---

## Success Criteria

### MVP (Minimum Viable Product)
- [ ] Curator can scan 1 expiration and return top 3 contracts
- [ ] Filtering works correctly (delta, DTE, OI, spread)
- [ ] Ranking algorithm implemented
- [ ] CLI tool works: `python curator_ticker.py NVDA call`

### Full Implementation
- [ ] Multi-expiration scanning (all valid DTEs)
- [ ] Caching reduces API calls by 80%+
- [ ] Integration with Judge in `curator_judge.py`
- [ ] Integration with Executor in `executor.py`
- [ ] Validation: Curator picks score 1.0+ higher than random (Judge)

### Stretch Goals
- [ ] Unusual activity detection (Vol/OI >1.25) working
- [ ] ATM proximity ranking implemented
- [ ] Support for broker.get_expirations() if API available
- [ ] Backtesting: Compare Curator-selected trades vs human-selected

---

## Timeline Estimate

| Phase | Estimated Time | Dependencies |
|-------|----------------|--------------|
| 1. Core Curator Module | 2-3 hours | Broker, Config |
| 2. Config Integration | 30 min | Phase 1 |
| 3. Expiration Handling | 1 hour | Phase 1 |
| 4. CLI Tool | 1 hour | Phases 1-3 |
| 5. Judge Integration | 1 hour | Phase 4, Judge |
| 6. Executor Integration | 1 hour | Phase 5, Executor |
| 7. Testing | 2 hours | All phases |
| 8. Documentation | 1 hour | All phases |
| **Total** | **9-10 hours** | |

---

## Next Steps

### Immediate Actions
1. **Review this plan** - Validate approach with user
2. **Create feature branch** - `claude/curator-implementation`
3. **Update config** - Add `curator` section to `default.yaml`
4. **Implement Phase 1** - Core `curator.py` module

### Questions for User
1. **Target grade tier:** Should Curator optimize for A-tier or B-tier by default?
2. **API limits:** Any known Alpaca rate limits we should be aware of?
3. **Priority:** Build Curator now, or wait until Scout is ready?
4. **Testing:** Paper trade Curator picks manually before wiring to Executor?

---

## Appendix: Example Output

### Curator CLI
```bash
$ python curator_ticker.py NVDA call

============================================================
MIKE-1 CURATOR - Finding best calls for NVDA
============================================================

Connected to Alpaca broker
Scanning expirations: 2026-01-10, 2026-01-17
Scanned 47 contracts
Found 12 passing filters
Scan time: 1847ms

🎯 Top 3 Candidates:

1. NVDA 135.00 call @ 2026-01-10
   Delta: 0.38 | DTE: 4 days | OI: 2,834
   Spread: 3.2% | Vol/OI: 1.42x
   🔥 UNUSUAL ACTIVITY DETECTED
   Curator Score: 87/100
   Reasons: Delta 0.38 (28pts), OI 2834 (15pts), Spread 3.2% (11pts), UNUSUAL: 1.42x (+20pts), Moneyness 0.98 (19pts)

2. NVDA 132.50 call @ 2026-01-10
   Delta: 0.42 | DTE: 4 days | OI: 1,923
   Spread: 4.1% | Vol/OI: 0.89x
   Curator Score: 79/100
   Reasons: Delta 0.42 (26pts), OI 1923 (15pts), Spread 4.1% (9pts), Moneyness 0.96 (18pts)

3. NVDA 137.50 call @ 2026-01-17
   Delta: 0.35 | DTE: 11 days | OI: 3,142
   Spread: 2.8% | Vol/OI: 1.05x
   Curator Score: 76/100
   Reasons: Delta 0.35 (30pts), OI 3142 (15pts), Spread 2.8% (12pts), Moneyness 1.00 (20pts)
```

### Curator + Judge Pipeline
```bash
$ python curator_judge.py NVDA call

============================================================
Curator + Judge Pipeline for NVDA call
============================================================

[Curator] Scanned 47 contracts, found 12 passing filters
[Curator] Top 3 candidates selected

[Judge] Evaluating candidate #1: NVDA 135.00 call @ 2026-01-10
  Grade: A-TIER | Score: 7.8/10

[Judge] Evaluating candidate #2: NVDA 132.50 call @ 2026-01-10
  Grade: A-TIER | Score: 7.2/10

[Judge] Evaluating candidate #3: NVDA 137.50 call @ 2026-01-17
  Grade: B-TIER | Score: 6.5/10

🏆 Best Option (Curator + Judge):

NVDA 135.00 call @ 2026-01-10
Grade: A-TIER
Score: 7.8/10 (Tech: 8.5, Liq: 8.2, Cat: 6.5)
Curator: 87/100

Reasoning:
  - Strong volume spike (3.2x avg)
  - Price above VWAP (+2.1%)
  - Delta in A-tier sweet spot (0.38)
  - Strong OI (2,834)
  - Tight spread (3.2%)
  - UNUSUAL ACTIVITY: Vol/OI 1.42x
  - DTE in sweet spot (4 days)
  - Catalyst: Earnings call mentioned AI chip demand
  - LLM: High confidence bullish sentiment

✅ READY TO EXECUTE (meets min_trade_grade: A)
```

---

## Conclusion

The Curator is **essential for full automation**. It's the bridge between "I like this stock" (Scout) and "Execute this specific contract" (Executor). Without it, humans must manually select strikes/expirations, making the system semi-automated at best.

**Recommendation:** Build Curator **before** Scout. Even with manual signals, Curator + Judge is immediately useful for finding optimal contracts.
