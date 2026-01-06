#!/usr/bin/env python
"""
Curator CLI - Find best option contracts for a ticker.

Usage:
    python curator_ticker.py NVDA call
    python curator_ticker.py SPY put --top 5
    python curator_ticker.py TSLA call --tier B
"""

import argparse
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from dotenv import load_dotenv
load_dotenv()

from mike1.modules.broker_factory import BrokerFactory
from mike1.modules.curator import Curator
from mike1.core.config import Config


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

    # Load config
    config = Config.load()

    # Connect to broker
    print("Connecting to broker...")
    broker = BrokerFactory.create("alpaca")
    if not broker.connect():
        print("❌ ERROR: Failed to connect to broker")
        sys.exit(1)
    print(f"✅ Connected to {broker.__class__.__name__}\n")

    # Create Curator
    curator = Curator(broker, config)

    # Find best options
    print(f"Scanning option chain for {args.symbol}...")
    result = curator.find_best_options(
        symbol=args.symbol,
        direction=args.direction,
        top_n=args.top,
        grade_tier=args.tier
    )

    # Print scan summary
    print(f"\n📊 Scan Summary:")
    print(f"  Contracts scanned: {result.total_contracts_scanned}")
    print(f"  Passed filters: {result.total_passing_filters}")
    print(f"  Scan time: {result.scan_time_ms:.0f}ms\n")

    # Print warnings
    if result.warnings:
        print("⚠️  Warnings:")
        for warning in result.warnings:
            print(f"  - {warning}")
        print()

    # Print candidates
    if not result.candidates:
        print("❌ No candidates found.\n")
        print("Possible reasons:")
        print("  - Ticker has low liquidity")
        print("  - No contracts in DTE range (3-14 days)")
        print("  - No contracts meet delta/OI/spread filters")
        print()
        return 1

    print(f"🎯 Top {len(result.candidates)} Candidate(s):\n")
    for i, candidate in enumerate(result.candidates, 1):
        print(f"{i}. {candidate.symbol} ${candidate.strike:.2f} {candidate.option_type.upper()} @ {candidate.expiration}")
        print(f"   Delta: {abs(candidate.delta):.3f} | DTE: {candidate.dte} days | OI: {candidate.open_interest:,}")
        print(f"   Bid/Ask: ${candidate.bid:.2f} / ${candidate.ask:.2f} (spread: {candidate.spread_pct*100:.1f}%)")
        print(f"   Volume: {candidate.volume:,} | Vol/OI: {candidate.vol_oi_ratio:.2f}x")

        if candidate.is_unusual_activity:
            print(f"   🔥 UNUSUAL ACTIVITY DETECTED")

        print(f"   Curator Score: {candidate.curator_score:.0f}/100")
        print(f"   Reasoning: {', '.join(candidate.ranking_reasons)}")
        print()

    print("✅ Use these candidates with Judge to get final score:")
    print(f"   python judge_ticker.py {args.symbol} {args.direction} \\")
    print(f"     --strike {result.candidates[0].strike} \\")
    print(f"     --expiration {result.candidates[0].expiration}")
    print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
