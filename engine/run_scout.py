#!/usr/bin/env python
"""
Scout CLI - Run signal detection scan

Usage:
    python run_scout.py
    python run_scout.py --clear-cooldowns
"""

import argparse
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from dotenv import load_dotenv
load_dotenv()

from mike1.modules.broker_factory import BrokerFactory
from mike1.modules.scout import Scout
from mike1.core.config import Config


def main():
    parser = argparse.ArgumentParser(description="Run Scout signal detection scan")
    parser.add_argument("--clear-cooldowns", action="store_true", help="Clear all cooldowns before scanning")
    args = parser.parse_args()

    print(f"\n{'='*70}")
    print(f"MIKE-1 SCOUT - Signal Detection")
    print(f"{'='*70}\n")

    # Load config
    print("Loading configuration...")
    config = Config.load()
    print(f"✅ Config loaded (environment: {config.environment})\n")

    # Connect to broker
    print("Connecting to broker...")
    broker = BrokerFactory.create("alpaca")
    if not broker.connect():
        print("❌ ERROR: Failed to connect to broker")
        sys.exit(1)
    print(f"✅ Connected to {broker.__class__.__name__}\n")

    # Create Scout
    scout = Scout(broker, config)

    # Clear cooldowns if requested
    if args.clear_cooldowns:
        print("🔄 Clearing all cooldowns...")
        scout.clear_cooldowns()
        print()

    # Show ticker sources
    print(f"📊 Ticker Sources:")
    print(f"  Manual enabled: {config.basket.manual.enabled}")
    if config.basket.manual.enabled:
        manual_tickers = config.basket._read_manual_file()
        if manual_tickers:
            print(f"  Manual tickers: {', '.join(manual_tickers)} ({len(manual_tickers)} total)")
        else:
            print(f"  Manual tickers: (none - add to {config.basket.manual.file})")

    print(f"  Core enabled: {config.basket.core.enabled}")
    if config.basket.core.enabled:
        print(f"  Core tickers: {', '.join(config.basket.core.tickers)}")

    print(f"  Categories enabled: {config.basket.categories.enabled}")
    all_tickers = config.basket.all_tickers
    print(f"  Total tickers to scan: {len(all_tickers)}")
    print()

    # Run scan
    print(f"[Scout] Scanning {len(all_tickers)} tickers...")
    print()

    result = scout.scan()

    # Print scan summary
    print(f"{'='*70}")
    print(f"SCAN COMPLETE")
    print(f"{'='*70}\n")
    print(f"📊 Scan Summary:")
    print(f"  Tickers scanned: {result.tickers_scanned}")
    print(f"  Signals detected: {result.signals_detected}")
    print(f"  Scan time: {result.scan_time_ms:.0f}ms\n")

    # Print warnings
    if result.warnings:
        print("⚠️  Warnings:")
        for warning in result.warnings:
            print(f"  - {warning}")
        print()

    # Print signals
    if not result.signals:
        print("❌ No signals detected.\n")
        print("Possible reasons:")
        print("  - No tickers meet catalyst criteria")
        print("  - Tickers on cooldown (use --clear-cooldowns)")
        print("  - Market conditions don't show clear setups")
        print()
        return 0

    print(f"🎯 Signals Detected ({len(result.signals)}):\n")
    for i, signal in enumerate(result.signals, 1):
        print(f"{i}. {signal.ticker} - {signal.direction.upper()}")
        print(f"   Catalyst: {signal.catalyst_type}")
        print(f"   Description: {signal.catalyst_description}")
        print(f"   Price: ${signal.current_price:.2f} | VWAP: ${signal.vwap:.2f if signal.vwap else 'N/A'}")
        if signal.volume:
            vol_ratio = signal.volume / signal.avg_volume if signal.avg_volume else 0
            print(f"   Volume: {signal.volume:,} ({vol_ratio:.1f}x avg)")
        if signal.rsi:
            print(f"   RSI: {signal.rsi:.1f}")
        print(f"   Priority: {signal.priority}")
        print(f"   ID: {signal.id}")
        print()

    print(f"{'='*70}")
    print("NEXT STEPS")
    print(f"{'='*70}\n")
    print("These signals are ready for Curator → Judge evaluation:")
    print()
    for i, signal in enumerate(result.signals[:3], 1):  # Top 3
        print(f"  {i}. Test {signal.ticker} {signal.direction}:")
        print(f"     python curator_judge.py {signal.ticker} {signal.direction}")
        print()

    print("Or run full pipeline:")
    print("  python run_full_pipeline.py  # (to be built)")
    print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
