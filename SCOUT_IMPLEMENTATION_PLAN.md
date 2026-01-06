# Scout (1st Layer) Implementation Plan

## Executive Summary

**Scout** is the "eyes" of MIKE-1 - the signal detection layer that identifies trading opportunities from market data. Scout monitors the configured ticker basket for catalysts (volume spikes, news events, technical setups) and creates `TradeSignal` objects for downstream evaluation.

### Scout's Single Responsibility
**Detect opportunities and create signals. Nothing more.**

Scout does NOT:
- ❌ Select option strikes/expirations (that's Curator's job)
- ❌ Score or grade trades (that's Judge's job)
- ❌ Execute trades (that's Executor's job)

Scout ONLY:
- ✅ Scans basket of tickers from config
- ✅ Detects catalysts (volume, news, technical setups)
- ✅ Creates `TradeSignal` objects with context
- ✅ Passes signals to Curator for contract selection

---

## Architecture Overview

### Position in Four Minds Flow

```
┌─────────┐    ┌─────────┐    ┌───────┐    ┌──────────┐
│  SCOUT  │───▶│ CURATOR │───▶│ JUDGE │───▶│ EXECUTOR │
└─────────┘    └─────────┘    └───────┘    └──────────┘
     │              │             │              │
  "NVDA has     "Best is      "That's      "Execute at
   volume       $135C         A-TIER        $2.45 ask
   spike →      1/10 @        7.8/10"       + trail"
   CALL"        0.38δ"
```

### Data Flow

```python
# 1. Scout detects catalyst
signal = scout.scan_ticker("NVDA")
# → TradeSignal(ticker="NVDA", direction="call", catalyst_type="volume_spike", ...)

# 2. Curator selects contract
candidates = curator.find_best_options(signal)
# → [OptionCandidate(strike=135, expiration="2026-01-10", delta=0.38, ...)]

# 3. Curator creates Trade with best candidate
trade = Trade(
    signal=signal,
    strike=candidates[0].strike,
    expiration=candidates[0].expiration,
    target_delta=candidates[0].delta,
    grade=None  # Judge will fill this
)

# 4. Judge evaluates
verdict = judge.grade(trade.ticker, trade.direction, trade.strike, trade.expiration)
trade.grade = verdict.grade

# 5. Executor executes if approved
if trade.grade == TradeGrade.A_TIER and governor.can_trade():
    executor.execute_trade(trade)
```

---

## What Scout Detects

### Catalyst Categories

| Catalyst Type | Trigger Condition | Signal Direction |
|---------------|-------------------|------------------|
| **Volume Spike** | Volume ≥ 2.5x avg + >1M shares | Price vs VWAP |
| **News Event** | Breaking news w/ LLM-detected urgency | LLM sentiment |
| **Earnings (Pre)** | 1-2 days before earnings | Neutral (IV play) |
| **Earnings (Post)** | Within 1 hour of earnings release | LLM sentiment |
| **RSI Extreme** | RSI <30 (oversold) or >70 (overbought) | Reversal direction |
| **VWAP Reversal** | Price crosses VWAP + volume confirmation | Cross direction |
| **Unusual Options Activity** | Vol/OI >1.25 + heavy flow | Call/Put flow bias |

### Signal Priority System

When multiple signals detected on same ticker, Scout prioritizes:

1. **Earnings (Post)** - 10 points (most time-sensitive)
2. **Earnings (Pre)** - 9 points
3. **News Event** - 8 points
4. **Unusual Options Activity** - 7 points
5. **Volume Spike** - 5 points
6. **Technical Setup** (RSI/VWAP) - 4 points

Higher priority signal wins if multiple detected in same scan cycle.

---

## Data Models

### TradeSignal (Already Exists)

Located in `engine/src/mike1/core/trade.py`:

```python
@dataclass
class TradeSignal:
    """
    A potential trade opportunity detected by the Scout.
    """
    # Identity
    id: str                    # "sig_20260106_001"
    ticker: str                # "NVDA"
    direction: str             # "call" or "put"

    # Catalyst (WHY trade this?)
    catalyst_type: str         # "volume_spike", "news", "earnings_pre", etc.
    catalyst_description: str  # Human-readable description
    catalyst_time: datetime    # When catalyst occurred

    # Market State (context for Judge/Curator)
    current_price: float
    vwap: Optional[float] = None
    volume: Optional[int] = None
    avg_volume: Optional[int] = None
    rsi: Optional[float] = None

    # Timestamps
    detected_at: datetime = field(default_factory=datetime.now)
```

**Scout fills in all fields.** Curator and Judge use this context for evaluation.

### ScoutResult (New)

```python
@dataclass
class ScoutResult:
    """
    Result of a single Scout scan cycle.
    """
    # Signals detected (sorted by priority desc)
    signals: list[TradeSignal]

    # Scan metadata
    tickers_scanned: int
    signals_detected: int
    scan_time_ms: float
    timestamp: datetime = field(default_factory=datetime.now)

    # Warnings/errors
    warnings: list[str] = field(default_factory=list)
```

---

## Core Algorithm

### Main Scan Loop

```python
class Scout:
    def __init__(self, config, broker, db):
        self.config = config
        self.broker = broker
        self.db = db
        self.detectors = [
            VolumeDetector(config, broker),
            NewsDetector(config, broker, llm_client),
            TechnicalDetector(config, broker),
            UOADetector(config, broker)  # Phase 5
        ]
        self.signal_cache = {}  # Prevent duplicate signals
        self.cooldown_tracker = {}  # Per-ticker cooldown

    def scan(self) -> ScoutResult:
        """
        Scan configured basket and return prioritized signals.

        Returns:
            ScoutResult with list of TradeSignals (sorted by priority)
        """
        start = time.time()
        signals = []

        # Get all tickers from config basket
        all_tickers = self.config.basket.all_tickers

        for ticker in all_tickers:
            # Skip if on cooldown
            if self._is_on_cooldown(ticker):
                continue

            # Run all detectors
            for detector in self.detectors:
                signal = detector.detect(ticker)

                if signal:
                    # Add priority score
                    signal.priority = self._get_priority(signal.catalyst_type)
                    signals.append(signal)

                    # Log to database
                    self.db.insert_signal(signal)

                    # Set cooldown to prevent re-scanning same ticker
                    self._set_cooldown(ticker)

                    # Only one signal per ticker per cycle
                    break

        # Sort by priority (highest first)
        signals.sort(key=lambda s: s.priority, reverse=True)

        elapsed_ms = (time.time() - start) * 1000

        return ScoutResult(
            signals=signals,
            tickers_scanned=len(all_tickers),
            signals_detected=len(signals),
            scan_time_ms=elapsed_ms
        )
```

### Detector Interface

Each detector implements this interface:

```python
class BaseDetector(ABC):
    """Base class for all Scout detectors."""

    @abstractmethod
    def detect(self, ticker: str) -> Optional[TradeSignal]:
        """
        Detect catalyst for a single ticker.

        Returns:
            TradeSignal if catalyst detected, None otherwise
        """
        pass
```

---

## Detector Implementations

### 1. Volume Spike Detector

```python
class VolumeDetector(BaseDetector):
    """Detects unusual volume spikes."""

    def detect(self, ticker: str) -> Optional[TradeSignal]:
        # Get volume data
        volume_data = self.broker.get_volume_data(ticker)
        current_vol = volume_data["current_volume"]
        avg_vol = volume_data["avg_volume"]

        # Check threshold
        spike_multiplier = self.config.scout.detectors.volume_spike.spike_multiplier  # 2.5
        min_volume = self.config.scout.detectors.volume_spike.min_volume  # 1M

        if current_vol < min_volume:
            return None

        vol_ratio = current_vol / avg_vol
        if vol_ratio < spike_multiplier:
            return None

        # Determine direction from price vs VWAP
        price = self.broker.get_stock_price(ticker)
        vwap_data = self.broker.get_vwap(ticker)
        vwap = vwap_data.get("vwap")

        if vwap and price > vwap:
            direction = "call"
        elif vwap and price < vwap:
            direction = "put"
        else:
            # No clear direction
            return None

        # Get RSI for additional context
        rsi = self.broker.get_rsi(ticker, period=14)

        # Create signal
        signal = TradeSignal(
            id=self._generate_signal_id(),
            ticker=ticker,
            direction=direction,
            catalyst_type="volume_spike",
            catalyst_description=f"Volume spike {vol_ratio:.1f}x average ({current_vol:,} shares)",
            catalyst_time=datetime.now(),
            current_price=price,
            vwap=vwap,
            volume=current_vol,
            avg_volume=avg_vol,
            rsi=rsi
        )

        return signal
```

### 2. News Event Detector

```python
class NewsDetector(BaseDetector):
    """Detects news-driven catalysts."""

    def __init__(self, config, broker, llm_client):
        super().__init__(config, broker)
        self.llm = llm_client

    def detect(self, ticker: str) -> Optional[TradeSignal]:
        # Get recent news (last 4 hours)
        news = self.broker.get_news(ticker, limit=5)

        if not news:
            return None

        # Check if news is recent enough
        latest = news[0]
        news_time = datetime.fromisoformat(latest["created_at"])
        age_hours = (datetime.now() - news_time).total_seconds() / 3600

        max_age = self.config.scout.detectors.news_catalyst.max_age_hours  # 4
        if age_hours > max_age:
            return None

        # Use LLM to assess urgency + sentiment
        headlines = [n["headline"] for n in news[:3]]

        prompt = f"""
        Assess these news headlines for {ticker}:
        {chr(10).join(f"- {h}" for h in headlines)}

        Is this breaking/urgent news that could move the stock?
        What's the sentiment (bullish/bearish/neutral)?

        Respond with JSON:
        {{
            "is_catalyst": true/false,
            "confidence": 0-1,
            "sentiment": "bullish"/"bearish"/"neutral",
            "direction": "call"/"put"/"none",
            "reasoning": "brief explanation"
        }}
        """

        llm_response = self.llm.query(prompt)

        if not llm_response["is_catalyst"]:
            return None

        min_confidence = self.config.scout.detectors.news_catalyst.min_confidence  # 0.7
        if llm_response["confidence"] < min_confidence:
            return None

        if llm_response["direction"] == "none":
            return None

        # Get market data
        price = self.broker.get_stock_price(ticker)
        volume_data = self.broker.get_volume_data(ticker)
        vwap_data = self.broker.get_vwap(ticker)
        rsi = self.broker.get_rsi(ticker, period=14)

        signal = TradeSignal(
            id=self._generate_signal_id(),
            ticker=ticker,
            direction=llm_response["direction"],
            catalyst_type="news_event",
            catalyst_description=f"{headlines[0][:100]}... (LLM: {llm_response['reasoning']})",
            catalyst_time=news_time,
            current_price=price,
            vwap=vwap_data.get("vwap"),
            volume=volume_data["current_volume"],
            avg_volume=volume_data["avg_volume"],
            rsi=rsi
        )

        return signal
```

### 3. Technical Setup Detector

```python
class TechnicalDetector(BaseDetector):
    """Detects RSI extremes and VWAP reversals."""

    def detect(self, ticker: str) -> Optional[TradeSignal]:
        # Get technical data
        price = self.broker.get_stock_price(ticker)
        rsi = self.broker.get_rsi(ticker, period=14)
        vwap_data = self.broker.get_vwap(ticker)
        vwap = vwap_data.get("vwap")
        volume_data = self.broker.get_volume_data(ticker)

        # Check volume confirmation
        vol_ratio = volume_data["current_volume"] / volume_data["avg_volume"]
        min_vol_confirm = self.config.scout.detectors.technical_setup.min_volume_confirmation  # 1.5

        if vol_ratio < min_vol_confirm:
            return None  # No volume confirmation

        # Check RSI extremes
        rsi_oversold = self.config.scout.detectors.technical_setup.rsi_oversold  # 30
        rsi_overbought = self.config.scout.detectors.technical_setup.rsi_overbought  # 70

        catalyst_type = None
        direction = None
        description = None

        if rsi and rsi < rsi_oversold:
            catalyst_type = "rsi_oversold"
            direction = "call"
            description = f"RSI oversold ({rsi:.1f}) + volume confirmation ({vol_ratio:.1f}x)"

        elif rsi and rsi > rsi_overbought:
            catalyst_type = "rsi_overbought"
            direction = "put"
            description = f"RSI overbought ({rsi:.1f}) + volume confirmation ({vol_ratio:.1f}x)"

        # Check VWAP reversal (alternative to RSI)
        elif vwap:
            # Price crossing above VWAP with volume = bullish
            if price > vwap and vol_ratio >= min_vol_confirm:
                catalyst_type = "vwap_reversal_bull"
                direction = "call"
                description = f"Price crossed above VWAP (+{((price/vwap - 1)*100):.1f}%) with volume"

            # Price crossing below VWAP with volume = bearish
            elif price < vwap and vol_ratio >= min_vol_confirm:
                catalyst_type = "vwap_reversal_bear"
                direction = "put"
                description = f"Price crossed below VWAP (-{((1 - price/vwap)*100):.1f}%) with volume"

        if not catalyst_type:
            return None

        signal = TradeSignal(
            id=self._generate_signal_id(),
            ticker=ticker,
            direction=direction,
            catalyst_type=catalyst_type,
            catalyst_description=description,
            catalyst_time=datetime.now(),
            current_price=price,
            vwap=vwap,
            volume=volume_data["current_volume"],
            avg_volume=volume_data["avg_volume"],
            rsi=rsi
        )

        return signal
```

---

## Configuration

### New Section: `scout`

Add to `config/default.yaml`:

```yaml
# =============================================================================
# SCOUT (Signal Detection)
# =============================================================================
scout:
  enabled: true

  # Scan interval (how often to check basket)
  scan_interval_seconds: 30

  # Cooldown period (prevent re-scanning same ticker)
  cooldown_seconds: 300  # 5 minutes

  # Detectors configuration
  detectors:
    volume_spike:
      enabled: true
      min_volume: 1_000_000        # Minimum shares to consider
      spike_multiplier: 2.5        # Current vol must be 2.5x avg

    news_catalyst:
      enabled: true
      max_age_hours: 4             # Only news from last 4 hours
      min_confidence: 0.7          # LLM confidence threshold
      keywords:                    # Optional keyword filter
        - earnings
        - FDA
        - acquisition
        - merger
        - guidance

    technical_setup:
      enabled: true
      rsi_oversold: 30
      rsi_overbought: 70
      min_volume_confirmation: 1.5  # Vol must be 1.5x avg

    unusual_options:
      enabled: false               # Phase 5 - not built yet
      vol_oi_ratio_min: 1.25
      min_premium_flow: 100000

  # Priority weights (higher = more important)
  priorities:
    earnings_post: 10
    earnings_pre: 9
    news_event: 8
    unusual_options: 7
    volume_spike: 5
    rsi_oversold: 4
    rsi_overbought: 4
    vwap_reversal_bull: 4
    vwap_reversal_bear: 4
```

### Pydantic Config Model

Add to `engine/src/mike1/core/config.py`:

```python
class VolumeDetectorConfig(BaseModel):
    enabled: bool = True
    min_volume: int = 1_000_000
    spike_multiplier: float = 2.5

class NewsDetectorConfig(BaseModel):
    enabled: bool = True
    max_age_hours: int = 4
    min_confidence: float = 0.7
    keywords: list[str] = []

class TechnicalDetectorConfig(BaseModel):
    enabled: bool = True
    rsi_oversold: int = 30
    rsi_overbought: int = 70
    min_volume_confirmation: float = 1.5

class UOADetectorConfig(BaseModel):
    enabled: bool = False
    vol_oi_ratio_min: float = 1.25
    min_premium_flow: int = 100000

class DetectorsConfig(BaseModel):
    volume_spike: VolumeDetectorConfig = Field(default_factory=VolumeDetectorConfig)
    news_catalyst: NewsDetectorConfig = Field(default_factory=NewsDetectorConfig)
    technical_setup: TechnicalDetectorConfig = Field(default_factory=TechnicalDetectorConfig)
    unusual_options: UOADetectorConfig = Field(default_factory=UOADetectorConfig)

class ScoutConfig(BaseModel):
    enabled: bool = True
    scan_interval_seconds: int = 30
    cooldown_seconds: int = 300
    detectors: DetectorsConfig = Field(default_factory=DetectorsConfig)
    priorities: dict[str, int] = Field(default_factory=dict)

class Config(BaseModel):
    # ... existing fields ...
    scout: ScoutConfig = Field(default_factory=ScoutConfig)
```

---

## Implementation Phases

### Phase 1: Core Scout Infrastructure (Foundation)

**Files:**
- `engine/src/mike1/modules/scout.py` (NEW)
- `engine/src/mike1/modules/detectors/__init__.py` (NEW)
- `engine/src/mike1/modules/detectors/base.py` (NEW)

**Tasks:**
1. Create `Scout` class with `scan()` method
2. Implement `BaseDetector` abstract class
3. Add cooldown tracking (prevent duplicate signals)
4. Add signal prioritization logic
5. Integrate with database (`db.insert_signal()`)

**Deliverables:**
- Scout skeleton that can be called by Engine
- Cooldown system working
- Database logging functional

**Estimated Time:** 3-4 hours

---

### Phase 2: Volume Spike Detector (Simplest)

**Files:**
- `engine/src/mike1/modules/detectors/volume.py` (NEW)

**Tasks:**
1. Implement `VolumeDetector` class
2. Use `broker.get_volume_data()` to fetch current vs avg volume
3. Direction detection from price vs VWAP
4. Create `TradeSignal` with full context

**Test:**
```bash
cd engine
python test_scout.py --detector volume --ticker NVDA
# Should detect volume spike if volume > 2.5x avg
```

**Estimated Time:** 2 hours

---

### Phase 3: Config Integration

**Files:**
- `config/default.yaml`
- `engine/src/mike1/core/config.py`

**Tasks:**
1. Add `scout` section to YAML (shown above)
2. Add Pydantic models to `config.py`
3. Wire Scout to read config on init
4. Test hot-reload (change spike_multiplier, reload works)

**Estimated Time:** 1 hour

---

### Phase 4: CLI Tool for Testing

**Files:**
- `engine/scout_ticker.py` (NEW)

**Purpose:** Manually test Scout detectors on a single ticker.

```python
#!/usr/bin/env python
"""
Scout CLI - Detect signals for a ticker.

Usage:
    python scout_ticker.py NVDA
    python scout_ticker.py SPY --detector volume
    python scout_ticker.py TSLA --all-detectors
"""

import argparse
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from dotenv import load_dotenv
load_dotenv()

from mike1.modules.broker_factory import BrokerFactory
from mike1.modules.scout import Scout
from mike1.utils.database import Database

def main():
    parser = argparse.ArgumentParser(description="Detect trading signals")
    parser.add_argument("symbol", help="Ticker symbol")
    parser.add_argument("--detector", choices=["volume", "news", "technical", "all"],
                       default="all", help="Which detector to run")
    args = parser.parse_args()

    print(f"\n{'='*60}")
    print(f"MIKE-1 SCOUT - Scanning {args.symbol}")
    print(f"{'='*60}\n")

    # Connect
    broker = BrokerFactory.create("alpaca")
    db = Database()
    scout = Scout(broker=broker, db=db)

    # Scan single ticker
    signal = scout.scan_ticker(args.symbol, detector_filter=args.detector)

    if signal:
        print(f"✅ SIGNAL DETECTED\n")
        print(f"Ticker: {signal.ticker}")
        print(f"Direction: {signal.direction.upper()}")
        print(f"Catalyst: {signal.catalyst_type}")
        print(f"Description: {signal.catalyst_description}")
        print(f"Price: ${signal.current_price:.2f}")
        if signal.vwap:
            print(f"VWAP: ${signal.vwap:.2f}")
        if signal.volume and signal.avg_volume:
            print(f"Volume: {signal.volume:,} ({signal.volume_ratio:.1f}x avg)")
        if signal.rsi:
            print(f"RSI: {signal.rsi:.1f}")
        print()
    else:
        print(f"❌ No signal detected for {args.symbol}\n")

    return 0

if __name__ == "__main__":
    sys.exit(main())
```

**Estimated Time:** 1.5 hours

---

### Phase 5: News Detector (High Value)

**Files:**
- `engine/src/mike1/modules/detectors/news.py` (NEW)

**Tasks:**
1. Implement `NewsDetector` class
2. Use `broker.get_news()` to fetch recent headlines
3. Use existing `llm_client` to assess urgency + sentiment
4. Filter by age (last 4 hours only)
5. Create signal with LLM reasoning in description

**Test:**
```bash
python scout_ticker.py NVDA --detector news
# Should detect if recent news exists
```

**Estimated Time:** 2.5 hours

---

### Phase 6: Technical Detector (RSI + VWAP)

**Files:**
- `engine/src/mike1/modules/detectors/technical.py` (NEW)

**Tasks:**
1. Implement `TechnicalDetector` class
2. RSI oversold (<30) → Call signal
3. RSI overbought (>70) → Put signal
4. VWAP reversals with volume confirmation
5. Require volume confirmation (>1.5x avg) for all signals

**Test:**
```bash
python scout_ticker.py SPY --detector technical
```

**Estimated Time:** 2 hours

---

### Phase 7: Engine Integration (Full Automation)

**Files:**
- `engine/src/mike1/engine.py`

**Tasks:**
1. Add Scout to Engine init
2. Modify `_poll_cycle()` to call Scout → Curator → Judge → Executor flow
3. Add signal deduplication
4. Respect daily trade limits
5. Log all signals to database

**Integration Pattern:**
```python
class Engine:
    def __init__(self, config):
        self.config = config
        self.broker = BrokerFactory.create(config.broker.type)
        self.db = Database()
        self.scout = Scout(self.broker, self.db, self.config)
        self.curator = Curator(self.broker, self.config)
        self.judge = Judge(self.broker, self.config)
        self.executor = Executor(self.broker, self.config)
        self.governor = RiskGovernor(self.config)

    def _poll_cycle(self):
        """Single polling cycle - Scout → Curator → Judge → Executor"""

        # 1. Check if armed and allowed to trade
        can_trade, reason = self.governor.can_trade()
        if not can_trade:
            logger.info("Trading blocked", reason=reason)
            return

        # 2. Scout scans for signals
        scout_result = self.scout.scan()
        logger.info("Scout scan complete",
                   signals_detected=len(scout_result.signals),
                   scan_time_ms=scout_result.scan_time_ms)

        # 3. Process each signal
        for signal in scout_result.signals:

            # 4. Curator finds best options
            curator_result = self.curator.find_best_options(
                signal=signal,
                top_n=3
            )

            if not curator_result.candidates:
                logger.warning("No valid options found",
                             ticker=signal.ticker,
                             direction=signal.direction)
                continue

            # 5. Judge evaluates top candidates
            verdicts = []
            for candidate in curator_result.candidates:
                verdict = self.judge.grade(
                    symbol=signal.ticker,
                    direction=signal.direction,
                    strike=candidate.strike,
                    expiration=candidate.expiration
                )
                verdicts.append((candidate, verdict))

            # Sort by Judge score
            verdicts.sort(key=lambda x: x[1].score, reverse=True)
            best_candidate, best_verdict = verdicts[0]

            # 6. Create Trade object
            trade = Trade(
                signal=signal,
                strike=best_candidate.strike,
                expiration=best_candidate.expiration,
                target_delta=best_candidate.delta,
                grade=best_verdict.grade,
                contracts=1,
                max_risk=self.config.risk.max_risk_per_trade
            )

            # 7. Executor executes if grade meets threshold
            result = self.executor.execute_trade(trade)

            if result:
                logger.info("Trade executed",
                           ticker=signal.ticker,
                           direction=signal.direction,
                           strike=trade.strike,
                           grade=trade.grade.value)

                # Check daily limit
                if self.governor.trades_today >= self.config.risk.max_trades_per_day:
                    logger.info("Daily trade limit reached, stopping")
                    break
            else:
                logger.info("Trade not executed",
                           ticker=signal.ticker,
                           grade=trade.grade.value,
                           min_required=self.config.scoring.min_trade_grade)

        # 8. Check exits on existing positions
        self.executor.poll()
```

**Estimated Time:** 3 hours

---

### Phase 8: Testing & Validation

**Files:**
- `engine/test_scout_integration.py` (NEW)
- `engine/src/mike1/modules/test_scout.py` (NEW)

**Unit Tests:**
1. Test each detector with mock data
2. Test cooldown logic
3. Test priority ranking
4. Test signal deduplication

**Integration Tests:**
1. Test full Scout → Curator → Judge flow
2. Test with live Alpaca data (paper trading)
3. Validate signals lead to higher Judge scores than random
4. Test daily limit enforcement

**Validation Metrics:**
- Scout should generate 3-8 signals per day (not too noisy)
- Scout signals should score 6.0+ with Judge (on average)
- False positive rate <50% (at least half should be B-tier or better)

**Estimated Time:** 3 hours

---

## Testing Strategy

### Manual Testing

```bash
# 1. Test single detector on one ticker
python scout_ticker.py NVDA --detector volume
python scout_ticker.py TSLA --detector news
python scout_ticker.py SPY --detector technical

# 2. Test full basket scan
python test_scout.py --scan-basket
# Should output all signals detected across basket

# 3. Test Scout → Curator → Judge flow
python test_scout_curator_judge.py NVDA
# Should show: Signal detected → Curator finds contracts → Judge scores them

# 4. Test live monitoring (dry run)
python run_mike1.py --dry-run
# Should log signals without executing trades
```

### Automated Testing

```bash
cd engine
python -m pytest test_scout_integration.py -v
python -m pytest src/mike1/modules/test_scout.py -v
```

---

## Dependencies

### New Python Dependencies
**None!** All functionality uses existing broker API, LLM client, and stdlib.

### Internal Dependencies
- ✅ `Broker.get_volume_data()` - Already implemented
- ✅ `Broker.get_stock_price()` - Already implemented
- ✅ `Broker.get_vwap()` - Already implemented
- ✅ `Broker.get_rsi()` - Already implemented
- ✅ `Broker.get_news()` - Already implemented
- ✅ `LLMClient` - Already implemented (Gemini)
- ✅ `Database.insert_signal()` - Already implemented
- ⏳ `Curator.find_best_options()` - **NOT YET BUILT** (Curator Phase 1-4)

**Critical Path:** Scout Phase 7 (Engine Integration) depends on Curator being built first.

---

## Risks & Mitigations

### Risk 1: Too Many Signals (Noise)

**Problem:** Scout generates 50+ signals per day, overwhelming Judge/Curator.

**Mitigation:**
- Strict thresholds (2.5x volume, not 1.5x)
- Cooldown period (5 minutes per ticker)
- Priority system (only process top-priority signals)
- Daily trade limit enforced by Governor

### Risk 2: LLM Rate Limits

**Problem:** News detector calls LLM for every news event = expensive.

**Mitigation:**
- Cache LLM responses (60 seconds)
- Keyword pre-filter (only call LLM if headline contains "earnings", "FDA", etc.)
- Limit news detector to 5 tickers per cycle

### Risk 3: Stale Market Data

**Problem:** Broker data is delayed, signal no longer valid by time it reaches Executor.

**Mitigation:**
- Accept this is unavoidable (all retail brokers have delay)
- Scout timestamps catalyst (`catalyst_time`)
- Judge can reject if catalyst is too old (>30 minutes)

### Risk 4: False Positives

**Problem:** Volume spike doesn't mean profitable trade.

**Mitigation:**
- Judge is the final filter (must score A-tier to execute)
- Curator ensures liquid contracts only
- Start with strict threshold (2.5x volume, not 2.0x)
- Track metrics: Which catalyst types are profitable?

---

## Success Criteria

### MVP (Minimum Viable Product)
- [ ] Scout can scan basket and detect volume spikes
- [ ] Signals logged to database
- [ ] CLI tool works: `python scout_ticker.py NVDA`
- [ ] Cooldown prevents duplicate signals

### Full Implementation
- [ ] All 3 detectors working (volume, news, technical)
- [ ] Scout → Curator → Judge → Executor flow end-to-end
- [ ] Engine polling loop integrated
- [ ] Signals generate 6.0+ average Judge score
- [ ] False positive rate <50%
- [ ] System generates 3-8 trades per day

### Stretch Goals
- [ ] Unusual options activity detector (Phase 9)
- [ ] Multi-timeframe confirmation (5min + 15min + 1hr)
- [ ] Earnings calendar integration
- [ ] Feedback loop: Track which signals → profitable trades

---

## Timeline Estimate

| Phase | Estimated Time | Dependencies |
|-------|----------------|--------------|
| 1. Core Infrastructure | 3-4 hours | Broker, Database |
| 2. Volume Detector | 2 hours | Phase 1 |
| 3. Config Integration | 1 hour | Phase 1 |
| 4. CLI Tool | 1.5 hours | Phases 1-3 |
| 5. News Detector | 2.5 hours | Phase 1, LLM |
| 6. Technical Detector | 2 hours | Phase 1 |
| 7. Engine Integration | 3 hours | **Curator must be built first** |
| 8. Testing | 3 hours | All phases |
| **Total** | **18-20 hours** | |

**Critical Path:** Curator (12-14 hours) + Scout (18-20 hours) = **30-34 hours** for full automation.

---

## Implementation Order (Recommended)

### Step 1: Build Curator FIRST ✅
- Curator provides immediate value (manual testing)
- Scout depends on Curator for Phase 7
- Timeline: 12-14 hours

### Step 2: Build Scout Phases 1-6
- Core infrastructure + detectors
- Test with CLI tools
- Timeline: 15-17 hours

### Step 3: Integrate Scout with Curator (Phase 7)
- Wire full flow in Engine
- End-to-end testing
- Timeline: 3 hours

### Step 4: Validation & Tuning
- Track metrics
- Adjust thresholds
- Timeline: Ongoing

---

## Appendix: Example Output

### Scout CLI

```bash
$ python scout_ticker.py NVDA

============================================================
MIKE-1 SCOUT - Scanning NVDA
============================================================

✅ SIGNAL DETECTED

Ticker: NVDA
Direction: CALL
Catalyst: volume_spike
Description: Volume spike 3.2x average (42,834,293 shares)
Price: $137.84
VWAP: $135.12
Volume: 42,834,293 (3.2x avg)
RSI: 58.3
```

### Scout Basket Scan

```bash
$ python test_scout.py --scan-basket

============================================================
MIKE-1 SCOUT - Scanning Basket (23 tickers)
============================================================

Scanned 23 tickers in 4.2 seconds

🎯 Signals Detected: 3

1. NVDA - CALL (volume_spike) - Priority: 5
   Volume spike 3.2x average

2. TSLA - PUT (news_event) - Priority: 8
   "Tesla recalls 2M vehicles for safety issue" (LLM: Bearish sentiment, high confidence)

3. SPY - CALL (rsi_oversold) - Priority: 4
   RSI oversold (28.4) + volume confirmation (1.8x)
```

### Full Flow Test

```bash
$ python test_scout_curator_judge.py NVDA

============================================================
Scout → Curator → Judge Pipeline for NVDA
============================================================

[Scout] Scanning NVDA...
✅ Signal detected: CALL (volume_spike)
   Volume: 42.8M (3.2x avg) | Price: $137.84 | VWAP: $135.12

[Curator] Finding best options...
Scanned 47 contracts, found 12 passing filters
Top 3 candidates selected

[Judge] Evaluating candidate #1: NVDA 135.00 call @ 2026-01-10
  Grade: A-TIER | Score: 7.8/10

[Judge] Evaluating candidate #2: NVDA 137.50 call @ 2026-01-10
  Grade: A-TIER | Score: 7.3/10

[Judge] Evaluating candidate #3: NVDA 140.00 call @ 2026-01-17
  Grade: B-TIER | Score: 6.7/10

🏆 Best Trade:
NVDA 135.00 call @ 2026-01-10
Grade: A-TIER (7.8/10)
Signal: volume_spike (3.2x avg)
Curator: 87/100
Judge: Technical 8.5, Liquidity 8.2, Catalyst 6.5

✅ READY TO EXECUTE (meets min_trade_grade: A)
```

---

## Conclusion

Scout is the **first layer** in MIKE-1's Four Minds architecture. Its single responsibility is **signal detection** - identifying market opportunities and packaging them as `TradeSignal` objects for downstream evaluation.

**Key Design Principles:**
1. **Single Responsibility:** Detect signals, nothing more
2. **Separation of Concerns:** Scout detects, Curator selects contracts, Judge scores, Executor executes
3. **Configurable Thresholds:** All detection rules in config, not hardcoded
4. **Incremental Testing:** Each detector can be tested independently
5. **Database Logging:** Track all signals for later analysis

**Critical Dependency:** Scout Phase 7 (Engine Integration) **requires Curator to be built first**.

**Recommended Path:**
1. Build Curator (12-14 hours) - Provides immediate value
2. Build Scout Phases 1-6 (15-17 hours) - Core detection + testing
3. Integrate Scout with Engine (3 hours) - Full automation achieved

**Total Time to Full Automation:** 30-34 hours

---

## Next Steps

### Immediate Actions
1. ✅ Review this plan with user
2. ⏳ Implement Curator (per CURATOR_IMPLEMENTATION_PLAN.md)
3. ⏳ After Curator working, start Scout Phase 1
4. ⏳ Test each detector independently before integration

### Questions for User
1. **Implementation order:** Agree to build Curator first, then Scout?
2. **Detector priority:** Which detector is most valuable? (Volume, News, or Technical?)
3. **Thresholds:** Are default values (2.5x volume, RSI 30/70) reasonable for your trading style?
4. **API limits:** Any known Alpaca rate limits for news/volume data?

---

**Status:** Ready for implementation pending Curator completion.
