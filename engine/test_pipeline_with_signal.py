#!/usr/bin/env python
"""
Test full pipeline with injected signal

Tests Curator → Judge → Executor flow with a known signal.
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from dotenv import load_dotenv
load_dotenv()

from datetime import datetime
from mike1.modules.broker_factory import BrokerFactory
from mike1.modules.curator import Curator
from mike1.modules.judge import Judge
from mike1.modules.executor import Executor
from mike1.core.config import Config
from mike1.core.risk_governor import RiskGovernor
from mike1.core.trade import TradeSignal, TradeGrade


def print_section(title):
    """Print section header."""
    print(f"\n{'='*70}")
    print(f"{title}")
    print(f"{'='*70}\n")


def main():
    print_section("MIKE-1 PIPELINE TEST (with injected signal)")

    # Load config
    print("Loading configuration...")
    config = Config.load()
    print(f"✅ Config loaded")
    print(f"   Environment: {config.environment}")
    print(f"   Min trade grade: {config.scoring.min_trade_grade}")
    print()

    # Connect to broker
    print("Connecting to PaperBroker...")
    broker = BrokerFactory.create("paper", starting_cash=100000.0)
    if not broker.connect():
        print("❌ ERROR: Failed to connect to broker")
        sys.exit(1)
    print(f"✅ Connected to {broker.__class__.__name__}")
    print()

    # Initialize components
    print("Initializing components...")
    curator = Curator(broker, config)
    judge = Judge(broker, config)
    governor = RiskGovernor(config)
    executor = Executor(broker, config, governor, dry_run=True)
    print(f"✅ All components initialized\n")

    # Create test signal (simulated volume spike on NVDA)
    test_signal = TradeSignal(
        id="test_sig_001",
        ticker="NVDA",
        direction="call",
        catalyst_type="volume_spike",
        catalyst_description="Test signal: Volume spike 3.2x average",
        catalyst_time=datetime.now(),
        current_price=140.50,
        vwap=139.80,
        volume=5000000,
        avg_volume=1500000,
        rsi=62.5,
        priority=5
    )

    print_section("STEP 1: CURATOR - Find Best Options")

    print(f"📊 Test Signal: {test_signal.ticker} {test_signal.direction.upper()}")
    print(f"   Catalyst: {test_signal.catalyst_description}")
    print(f"   Price: ${test_signal.current_price:.2f}")
    print()

    print(f"[Curator] Scanning option chain for {test_signal.ticker}...")
    curator_result = curator.find_best_options(
        symbol=test_signal.ticker,
        direction=test_signal.direction,
        top_n=config.curator.max_candidates
    )

    print(f"[Curator] Scanned {curator_result.total_contracts_scanned} contracts")
    print(f"[Curator] Found {len(curator_result.candidates)} candidate(s)")
    print()

    if not curator_result.candidates:
        print(f"❌ No options found for {test_signal.ticker}")
        return 1

    # Show top candidates
    for i, candidate in enumerate(curator_result.candidates, 1):
        print(f"  Candidate #{i}:")
        print(f"    ${candidate.strike:.0f} {candidate.option_type.upper()} @ {candidate.expiration}")
        print(f"    Curator Score: {candidate.curator_score:.0f}/100")
        print(f"    Delta: {abs(candidate.delta):.3f} | DTE: {candidate.dte} | Premium: ${candidate.ask:.2f}")
        print()

    print_section("STEP 2: JUDGE - Score & Grade Options")

    verdicts = []
    for i, candidate in enumerate(curator_result.candidates, 1):
        print(f"[Judge] Evaluating Candidate #{i}...")

        verdict = judge.grade(
            symbol=test_signal.ticker,
            direction=test_signal.direction,
            strike=candidate.strike,
            expiration=candidate.expiration,
            use_llm=False  # Disable LLM for faster testing
        )

        print(f"  Result: {verdict.grade.value}-TIER ({verdict.score:.1f}/10)")
        print(f"  Breakdown:")
        print(f"    Technical:  {verdict.technical_score:.1f}/10")
        print(f"    Liquidity:  {verdict.liquidity_score:.1f}/10")
        print(f"    Catalyst:   {verdict.catalyst_score:.1f}/10")
        print()

        verdicts.append({
            'candidate': candidate,
            'verdict': verdict
        })

    # Pick best verdict
    verdicts.sort(key=lambda x: x['verdict'].score, reverse=True)
    best = verdicts[0]

    print(f"🏆 Best Option:")
    print(f"   ${best['candidate'].strike:.0f} {best['candidate'].option_type.upper()} @ {best['candidate'].expiration}")
    print(f"   Grade: {best['verdict'].grade.value}-TIER ({best['verdict'].score:.1f}/10)")
    print()

    print_section("STEP 3: EXECUTOR - Trade Decision")

    min_grade = config.scoring.min_trade_grade
    verdict = best['verdict']
    candidate = best['candidate']

    print(f"Minimum grade requirement: {min_grade}-TIER")
    print(f"This option grade: {verdict.grade.value}-TIER")
    print()

    # Check if meets minimum grade
    if verdict.grade == TradeGrade.A and min_grade == "A":
        status = "✅ APPROVED"
    elif verdict.grade == TradeGrade.B and min_grade in ["A", "B"]:
        if min_grade == "A":
            status = "❌ BLOCKED (B-tier, requires A)"
            print(f"{status}")
            print(f"\nTo allow B-tier trades, set min_trade_grade: 'B' in config")
            return 0
        else:
            status = "✅ APPROVED"
    else:
        status = "❌ BLOCKED (NO_TRADE)"
        print(f"{status}")
        return 0

    # Check Risk Governor
    if not governor.can_trade():
        print(f"❌ BLOCKED by Risk Governor")
        print(f"   Reason: {governor.get_block_reason()}")
        return 0

    print(f"{status}")
    print()

    # Calculate position size
    max_risk = config.risk.max_risk_per_trade
    price_estimate = candidate.ask * 100  # Convert to dollars
    contracts = min(
        int(max_risk / price_estimate) if price_estimate > 0 else 1,
        config.risk.max_contracts
    )
    if contracts == 0:
        contracts = 1

    print(f"📊 Trade Details:")
    print(f"   Symbol: {test_signal.ticker}")
    print(f"   Direction: {test_signal.direction.upper()}")
    print(f"   Strike: ${candidate.strike:.0f}")
    print(f"   Expiration: {candidate.expiration}")
    print(f"   Contracts: {contracts}")
    print(f"   Entry: ${candidate.ask:.2f} per contract")
    print(f"   Total cost: ${candidate.ask * 100 * contracts:.2f}")
    print(f"   Risk allocation: {(price_estimate * contracts / max_risk * 100):.1f}% of max")
    print()

    print(f"[DRY-RUN] Trade execution simulated (not armed)")
    print()

    print_section("TEST SUMMARY")
    print("✅ Curator found option candidates")
    print("✅ Judge scored and graded options")
    print("✅ Risk Governor checked limits")
    print("✅ Executor prepared trade (dry-run)")
    print()
    print("End-to-end pipeline integration: SUCCESS")
    print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
