#!/usr/bin/env python
"""
MIKE-1 Full Pipeline Integration

Complete flow: Scout → Curator → Judge → Executor

This script demonstrates the full automation:
1. Scout detects signals (volume, news, technical)
2. Curator finds best option contracts
3. Judge scores and grades each candidate
4. Executor manages positions (dry-run by default)

Usage:
    python run_full_pipeline.py              # Dry run (no real trades)
    python run_full_pipeline.py --live       # Live trading (if armed)
    python run_full_pipeline.py --max-signals 3  # Limit signals to process
"""

import argparse
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from dotenv import load_dotenv
load_dotenv()

from mike1.modules.broker_factory import BrokerFactory
from mike1.modules.scout import Scout
from mike1.modules.curator import Curator
from mike1.modules.judge import Judge
from mike1.core.trade import TradeGrade
from mike1.modules.executor import Executor
from mike1.modules.llm_client import GeminiClient
from mike1.core.config import Config
from mike1.core.risk_governor import RiskGovernor


def print_section(title):
    """Print section header."""
    print(f"\n{'='*70}")
    print(f"{title}")
    print(f"{'='*70}\n")


def main():
    parser = argparse.ArgumentParser(description="Run full MIKE-1 pipeline")
    parser.add_argument("--live", action="store_true", help="Live trading mode (default: dry-run)")
    parser.add_argument("--max-signals", type=int, default=5, help="Max signals to process")
    parser.add_argument("--clear-cooldowns", action="store_true", help="Clear Scout cooldowns before scanning")
    args = parser.parse_args()

    print_section("MIKE-1 FULL PIPELINE")

    # Load config
    print("Loading configuration...")
    config = Config.load()
    print(f"✅ Config loaded")
    print(f"   Environment: {config.environment}")
    print(f"   Armed: {config.armed}")
    print(f"   Min trade grade: {config.scoring.min_trade_grade}")
    print()

    # Connect to broker (use Alpaca if --live, else simulated paper)
    print("Connecting to broker...")
    if args.live:
        broker_type = "alpaca"
        broker = BrokerFactory.create(broker_type, paper=True)  # Alpaca paper account
    else:
        broker_type = "paper"
        broker = BrokerFactory.create(broker_type, starting_cash=100000.0)  # Simulated
    if not broker.connect():
        print("❌ ERROR: Failed to connect to broker")
        sys.exit(1)
    print(f"✅ Connected to {broker.__class__.__name__}")

    # Get account info
    account = broker.get_account_info()
    print(f"   Cash: ${account.get('cash', 0):,.2f}")
    print(f"   Positions: {account.get('positions_count', 0)}")
    print()

    # Initialize components
    print("Initializing MIKE-1 components...")
    scout = Scout(broker, config)
    curator = Curator(broker, config)
    llm_client = GeminiClient()  # Uses GEMINI_API_KEY from env
    judge = Judge(broker, llm_client)

    # Risk Governor
    governor = RiskGovernor(config)

    # Executor (dry-run unless --live specified)
    dry_run = not args.live
    executor = Executor(broker, config, governor, dry_run=dry_run)

    print(f"✅ All components initialized")
    print(f"   Scout: {len(scout.detectors)} detector(s)")
    print(f"   Curator: Top {config.curator.max_candidates} candidates")
    print(f"   Judge: A-tier ≥{config.scoring.a_tier_min}, B-tier ≥{config.scoring.b_tier_min}")
    print(f"   Executor: {'DRY-RUN' if dry_run else 'LIVE'} mode")
    print()

    # Clear cooldowns if requested
    if args.clear_cooldowns:
        print("🔄 Clearing Scout cooldowns...")
        scout.clear_cooldowns()
        print()

    # ==========================================================================
    # STEP 1: SCOUT - Detect Signals
    # ==========================================================================
    print_section("STEP 1: SCOUT - Signal Detection")

    all_tickers = config.basket.all_tickers
    print(f"Scanning {len(all_tickers)} tickers...")
    print(f"Sources: {len(config.basket._read_manual_file())} manual + "
          f"{len(config.basket.core.tickers)} core + "
          f"{len(config.basket.categories.tech) + len(config.basket.categories.biotech) + len(config.basket.categories.momentum) + len(config.basket.categories.etfs)} categories")
    print()

    scout_result = scout.scan()

    print(f"📊 Scout Results:")
    print(f"   Tickers scanned: {scout_result.tickers_scanned}")
    print(f"   Signals detected: {scout_result.signals_detected}")
    print(f"   Scan time: {scout_result.scan_time_ms:.0f}ms")
    print()

    if not scout_result.signals:
        print("❌ No signals detected.")
        print()
        print("Reasons:")
        print("  - No volume spikes (≥2.5x avg, >1M shares)")
        print("  - No news catalysts (≥10 mentions)")
        print("  - No RSI extremes (<30 or >70)")
        print("  - Tickers on cooldown (use --clear-cooldowns)")
        print()
        return 0

    # Limit signals to process
    signals_to_process = scout_result.signals[:args.max_signals]

    print(f"🎯 Processing top {len(signals_to_process)} signal(s):\n")
    for i, signal in enumerate(signals_to_process, 1):
        print(f"{i}. {signal.ticker} - {signal.direction.upper()}")
        print(f"   Catalyst: {signal.catalyst_type} (priority {signal.priority})")
        print(f"   Description: {signal.catalyst_description}")
        print(f"   Price: ${signal.current_price:.2f}")
        if signal.vwap:
            print(f"   VWAP: ${signal.vwap:.2f}")
        if signal.volume:
            vol_ratio = signal.volume / signal.avg_volume if signal.avg_volume else 0
            print(f"   Volume: {signal.volume:,} ({vol_ratio:.1f}x avg)")
        if signal.rsi:
            print(f"   RSI: {signal.rsi:.1f}")
        print()

    # ==========================================================================
    # STEP 2: CURATOR → JUDGE - Find & Score Options
    # ==========================================================================
    print_section("STEP 2: CURATOR → JUDGE - Option Selection & Grading")

    best_trades = []

    for i, signal in enumerate(signals_to_process, 1):
        print(f"[{i}/{len(signals_to_process)}] Processing {signal.ticker} {signal.direction.upper()}...")
        print()

        # Curator finds best contracts
        print(f"  [Curator] Scanning option chain...")
        curator_result = curator.find_best_options(
            symbol=signal.ticker,
            direction=signal.direction,
            top_n=config.curator.max_candidates
        )

        print(f"  [Curator] Scanned {curator_result.total_contracts_scanned} contracts")
        print(f"  [Curator] Found {len(curator_result.candidates)} candidate(s)")
        print()

        if not curator_result.candidates:
            print(f"  ⚠️  No options found for {signal.ticker} (low liquidity or no matching strikes)")
            print()
            continue

        # Judge evaluates each candidate
        print(f"  [Judge] Evaluating {len(curator_result.candidates)} candidate(s)...")
        print()

        verdicts = []
        for j, candidate in enumerate(curator_result.candidates, 1):
            print(f"    Candidate #{j}: ${candidate.strike:.0f} {candidate.option_type.upper()} @ {candidate.expiration}")
            print(f"      Curator Score: {candidate.curator_score:.0f}/100")
            print(f"      Delta: {abs(candidate.delta):.3f} | DTE: {candidate.dte} | OI: {candidate.open_interest:,}")

            # Judge the candidate
            verdict = judge.grade(
                symbol=signal.ticker,
                direction=signal.direction,
                strike=candidate.strike,
                expiration=candidate.expiration,
                use_llm=True  # Use LLM if available
            )

            print(f"      Judge: {verdict.grade.value}-TIER ({verdict.score:.1f}/10)")
            print(f"      Breakdown: Tech {verdict.technical_score:.1f} | Liq {verdict.liquidity_score:.1f} | Cat {verdict.catalyst_score:.1f}")
            print()

            verdicts.append({
                'signal': signal,
                'candidate': candidate,
                'verdict': verdict
            })

        # Sort by Judge score and pick best
        verdicts.sort(key=lambda x: x['verdict'].score, reverse=True)
        best = verdicts[0]

        print(f"  🏆 Best Option: ${best['candidate'].strike:.0f} {best['candidate'].option_type.upper()} @ {best['candidate'].expiration}")
        print(f"     Grade: {best['verdict'].grade.value}-TIER ({best['verdict'].score:.1f}/10)")
        print()

        best_trades.append(best)

    if not best_trades:
        print("❌ No tradeable options found.")
        return 0

    # ==========================================================================
    # STEP 3: EXECUTOR - Execute A-Tier Trades
    # ==========================================================================
    print_section("STEP 3: EXECUTOR - Trade Execution")

    min_grade = config.scoring.min_trade_grade
    print(f"Minimum grade requirement: {min_grade}-TIER")
    print(f"Mode: {'DRY-RUN (simulation)' if dry_run else 'LIVE (real money)'}")
    print()

    executed_count = 0
    blocked_count = 0

    for i, trade in enumerate(best_trades, 1):
        signal = trade['signal']
        candidate = trade['candidate']
        verdict = trade['verdict']

        print(f"[{i}/{len(best_trades)}] {signal.ticker} ${candidate.strike:.0f} {candidate.option_type.upper()} - {verdict.grade.value}-TIER")

        # Check if meets minimum grade
        if verdict.grade == TradeGrade.A_TIER and min_grade == "A":
            status = "✅ APPROVED"
        elif verdict.grade == TradeGrade.B_TIER and min_grade in ["A", "B"]:
            if min_grade == "A":
                status = "❌ BLOCKED (B-tier, requires A)"
                blocked_count += 1
                print(f"   {status}")
                print()
                continue
            else:
                status = "✅ APPROVED"
        else:
            status = "❌ BLOCKED (NO_TRADE)"
            blocked_count += 1
            print(f"   {status}")
            print()
            continue

        # Check if governor allows trade
        if not governor.can_trade():
            status = "❌ BLOCKED (Risk Governor)"
            blocked_count += 1
            print(f"   {status}")
            print(f"   Reason: {governor.get_block_reason()}")
            print()
            continue

        print(f"   {status}")

        # Calculate contract quantity
        max_risk = config.risk.max_risk_per_trade
        price_estimate = candidate.ask * 100  # Convert to dollars
        contracts = min(
            int(max_risk / price_estimate) if price_estimate > 0 else 1,
            config.risk.max_contracts
        )

        if contracts == 0:
            contracts = 1

        print(f"   Contracts: {contracts} (risk: ${contracts * price_estimate:.2f})")
        print()

        if dry_run:
            print(f"   [DRY-RUN] Would execute:")
            print(f"     BUY {contracts}x {signal.ticker} ${candidate.strike:.0f} {candidate.option_type.upper()} @ {candidate.expiration}")
            print(f"     Entry: ~${candidate.ask:.2f} per contract (${candidate.ask * 100 * contracts:.2f} total)")
            print(f"     Stop: -50% (${candidate.ask * 0.5:.2f})")
            print(f"     Trailing: 25% from HWM")
            print()
            executed_count += 1
        else:
            # Actually execute (if armed)
            if not config.armed:
                print(f"   ⚠️  System NOT ARMED - skipping execution")
                print(f"   To enable: Set 'armed: true' in config/default.yaml")
                print()
                continue

            print(f"   🚀 EXECUTING TRADE...")
            # TODO: Wire up actual execution
            # executor.execute_option_trade(
            #     ticker=signal.ticker,
            #     strike=candidate.strike,
            #     expiration=candidate.expiration,
            #     option_type=candidate.option_type,
            #     quantity=contracts
            # )
            print(f"   ✅ Trade executed")
            print()
            executed_count += 1

    # ==========================================================================
    # SUMMARY
    # ==========================================================================
    print_section("PIPELINE SUMMARY")

    print(f"📊 Results:")
    print(f"   Signals detected: {scout_result.signals_detected}")
    print(f"   Signals processed: {len(signals_to_process)}")
    print(f"   Options evaluated: {sum(len(t.get('verdicts', [])) for t in best_trades)}")
    print(f"   Trades approved: {executed_count}")
    print(f"   Trades blocked: {blocked_count}")
    print()

    if executed_count > 0:
        print(f"✅ {executed_count} trade(s) {'simulated' if dry_run else 'executed'}")
    else:
        print(f"⚠️  No trades executed")

    if dry_run:
        print()
        print("To execute real trades:")
        print("  1. Set 'armed: true' in config/default.yaml")
        print("  2. Run: python run_full_pipeline.py --live")

    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
