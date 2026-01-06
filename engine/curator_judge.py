#!/usr/bin/env python
"""
Curator → Judge Pipeline

Full flow: Curator finds best options → Judge scores each → Return winner

Usage:
    python curator_judge.py NVDA call
    python curator_judge.py SPY put --top 5
    python curator_judge.py TSLA call --no-llm
"""

import argparse
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from dotenv import load_dotenv
load_dotenv()

from mike1.modules.broker_factory import BrokerFactory
from mike1.modules.curator import Curator
from mike1.modules.judge import Judge
from mike1.core.config import Config
from mike1.core.trade import TradeGrade


def main():
    parser = argparse.ArgumentParser(description="Curator → Judge pipeline")
    parser.add_argument("symbol", help="Ticker symbol (e.g., NVDA)")
    parser.add_argument("direction", choices=["call", "put"], help="Trade direction")
    parser.add_argument("--top", type=int, default=3, help="Number of candidates from Curator")
    parser.add_argument("--tier", choices=["A", "B"], default="A", help="Grade tier to target")
    parser.add_argument("--no-llm", action="store_true", help="Skip LLM catalyst scoring")
    args = parser.parse_args()

    print(f"\n{'='*70}")
    print(f"Curator → Judge Pipeline for {args.symbol} {args.direction.upper()}")
    print(f"{'='*70}\n")

    # Load config
    config = Config.load()

    # Connect to broker
    print("Connecting to broker...")
    broker = BrokerFactory.create("alpaca")
    if not broker.connect():
        print("❌ ERROR: Failed to connect to broker")
        sys.exit(1)
    print(f"✅ Connected to {broker.__class__.__name__}\n")

    # Create Curator and Judge
    curator = Curator(broker, config)
    judge = Judge(broker, config)

    # STEP 1: Curator finds best options
    print(f"[Curator] Scanning option chain for {args.symbol}...")
    curator_result = curator.find_best_options(
        symbol=args.symbol,
        direction=args.direction,
        top_n=args.top,
        grade_tier=args.tier
    )

    print(f"[Curator] Scanned {curator_result.total_contracts_scanned} contracts")
    print(f"[Curator] Found {curator_result.total_passing_filters} passing filters")
    print(f"[Curator] Top {len(curator_result.candidates)} candidates selected")
    print(f"[Curator] Scan time: {curator_result.scan_time_ms:.0f}ms\n")

    if curator_result.warnings:
        print("⚠️  Curator Warnings:")
        for warning in curator_result.warnings:
            print(f"  - {warning}")
        print()

    if not curator_result.candidates:
        print("❌ No candidates found. Cannot proceed to Judge.\n")
        print("Try:")
        print(f"  - Different ticker (this one may have low liquidity)")
        print(f"  - Different tier (--tier B for wider delta range)")
        print()
        return 1

    # STEP 2: Judge evaluates each candidate
    print(f"[Judge] Evaluating {len(curator_result.candidates)} candidate(s)...\n")

    verdicts = []
    for i, candidate in enumerate(curator_result.candidates, 1):
        print(f"[Judge] Candidate #{i}: {candidate.symbol} ${candidate.strike:.2f} {candidate.option_type.upper()} @ {candidate.expiration}")
        print(f"        Curator Score: {candidate.curator_score:.0f}/100")

        # Judge the candidate
        verdict = judge.grade(
            symbol=args.symbol,
            direction=args.direction,
            strike=candidate.strike,
            expiration=candidate.expiration,
            use_llm=not args.no_llm
        )

        print(f"        Judge Grade: {verdict.grade.value}-TIER")
        print(f"        Judge Score: {verdict.score:.1f}/10")
        print(f"        Breakdown: Tech {verdict.technical_score:.1f}, Liq {verdict.liquidity_score:.1f}, Cat {verdict.catalyst_score:.1f}")
        print()

        verdicts.append((candidate, verdict))

    # STEP 3: Sort by Judge score (highest first)
    verdicts.sort(key=lambda x: x[1].score, reverse=True)
    best_candidate, best_verdict = verdicts[0]

    # STEP 4: Display winner
    print("=" * 70)
    print("🏆 BEST OPTION (Curator + Judge)")
    print("=" * 70)
    print()
    print(f"Contract: {best_candidate.symbol} ${best_candidate.strike:.2f} {best_candidate.option_type.upper()} @ {best_candidate.expiration}")
    print(f"  Delta: {abs(best_candidate.delta):.3f} | DTE: {best_candidate.dte} days")
    print(f"  OI: {best_candidate.open_interest:,} | Spread: {best_candidate.spread_pct*100:.1f}%")
    if best_candidate.is_unusual_activity:
        print(f"  🔥 Unusual Activity: Vol/OI {best_candidate.vol_oi_ratio:.2f}x")
    print()
    print(f"Curator Score: {best_candidate.curator_score:.0f}/100")
    print(f"  {', '.join(best_candidate.ranking_reasons)}")
    print()
    print(f"Judge Grade: {best_verdict.grade.value}-TIER")
    print(f"Judge Score: {best_verdict.score:.1f}/10")
    print(f"  Technical: {best_verdict.technical_score:.1f}/10")
    print(f"  Liquidity: {best_verdict.liquidity_score:.1f}/10")
    print(f"  Catalyst: {best_verdict.catalyst_score:.1f}/10")
    print()

    # Show reasoning
    if best_verdict.reasoning:
        print("Judge Reasoning:")
        for reason in best_verdict.reasoning[:10]:  # Top 10 reasons
            print(f"  • {reason}")
        print()

    # STEP 5: Execution readiness
    min_grade = config.scoring.min_trade_grade
    print("=" * 70)

    if best_verdict.grade == TradeGrade.A_TIER and min_grade == "A":
        print("✅ READY TO EXECUTE (meets min_trade_grade: A)")
        print()
        print("Next steps:")
        print("  1. Arm the system: Set 'armed: true' in config")
        print("  2. Execute via Executor or wait for Scout to detect signal")
    elif best_verdict.grade == TradeGrade.B_TIER and min_grade in ["A", "B"]:
        if min_grade == "A":
            print("⚠️  B-TIER - BLOCKED (min_trade_grade is 'A')")
            print()
            print("To allow B-tier trades:")
            print("  - Change config: scoring.min_trade_grade: 'B'")
            print("  - Or wait for an A-tier opportunity")
        else:
            print("✅ READY TO EXECUTE (meets min_trade_grade: B)")
    else:
        print("❌ NO TRADE (does not meet minimum grade)")
        print()
        print("This setup does not meet the minimum quality threshold.")
        print(f"  Min required: {min_grade}-TIER")
        print(f"  Best found: {best_verdict.grade.value}-TIER")

    print("=" * 70)
    print()

    # STEP 6: Show all candidates for comparison
    if len(verdicts) > 1:
        print("📊 All Candidates Ranked by Judge Score:")
        print()
        for i, (candidate, verdict) in enumerate(verdicts, 1):
            status = "🏆 WINNER" if i == 1 else f"   #{i}"
            print(f"{status}  ${candidate.strike:.2f} {candidate.option_type.upper()} @ {candidate.expiration}")
            print(f"        Curator: {candidate.curator_score:.0f}/100 | Judge: {verdict.score:.1f}/10 ({verdict.grade.value}-TIER)")
        print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
