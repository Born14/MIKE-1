#!/usr/bin/env python
"""
Execute 0DTE Option Trade

Quick script to execute a 0DTE trade on a specific ticker.
This is optimized for mobile/GitHub Actions execution.

Usage:
    python execute_0dte.py QQQ call
    python execute_0dte.py SPY put
"""

import argparse
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from dotenv import load_dotenv
load_dotenv()

from datetime import datetime, timedelta
from mike1.modules.broker_factory import BrokerFactory
from mike1.modules.curator import Curator
from mike1.modules.judge import Judge
from mike1.modules.executor import Executor
from mike1.modules.llm_client import GeminiClient
from mike1.core.config import Config
from mike1.core.risk_governor import RiskGovernor
from mike1.core.trade import Trade, TradeSignal


def main():
    parser = argparse.ArgumentParser(description="Execute 0DTE option trade")
    parser.add_argument("symbol", help="Ticker symbol (e.g., QQQ)")
    parser.add_argument("direction", choices=["call", "put"], help="Trade direction")
    parser.add_argument("--dry-run", action="store_true", help="Simulate without executing")
    args = parser.parse_args()

    print(f"\n{'='*70}")
    print(f"🚀 EXECUTE 0DTE {args.direction.upper()} on {args.symbol}")
    print(f"{'='*70}\n")

    # Load config
    config = Config.load()
    print(f"Config: {config.environment} | Armed: {config.armed}")

    # Connect to broker
    print("Connecting to Alpaca...")
    broker = BrokerFactory.create("alpaca", paper=True)
    if not broker.connect():
        print("❌ Failed to connect to broker")
        return 1

    account = broker.get_account_info()
    print(f"✅ Connected - Cash: ${account.get('cash', 0):,.2f}\n")

    # Initialize components
    curator = Curator(broker, config)
    llm_client = GeminiClient()
    judge = Judge(broker, llm_client)
    governor = RiskGovernor(config)
    executor = Executor(broker, config, governor, dry_run=args.dry_run)

    # Get current market data
    print(f"Fetching {args.symbol} market data...")
    price = broker.get_stock_price(args.symbol)
    vwap_data = broker.get_vwap(args.symbol)
    vwap = vwap_data.get('vwap', 0) if vwap_data else 0

    print(f"Price: ${price:.2f} | VWAP: ${vwap:.2f}")

    # Check direction makes sense
    if args.direction == "call" and price < vwap:
        print(f"⚠️  WARNING: Price below VWAP but trading CALL (contrarian trade)")
    elif args.direction == "put" and price > vwap:
        print(f"⚠️  WARNING: Price above VWAP but trading PUT (contrarian trade)")
    print()

    # Find 0DTE option
    print(f"Finding best 0DTE {args.direction}...")

    # Find today's expiration
    today = datetime.now().date()
    expiration = today.strftime("%Y-%m-%d")

    result = curator.find_best_options(
        symbol=args.symbol,
        direction=args.direction,
        top_n=1  # Just get the best one
    )

    if not result.candidates:
        print("❌ No 0DTE options found")
        print("   - Check if market is open")
        print("   - Check if options are available for this ticker")
        return 1

    candidate = result.candidates[0]

    # Check if it's actually 0DTE
    if candidate.dte != 0:
        print(f"⚠️  WARNING: Found {candidate.dte}DTE option (not 0DTE)")
        print(f"   No 0DTE contracts available, using closest: {candidate.expiration}")

    print(f"✅ Found: ${candidate.strike:.0f} {candidate.option_type.upper()} @ {candidate.expiration}")
    print(f"   DTE: {candidate.dte} | Delta: {abs(candidate.delta):.3f} | OI: {candidate.open_interest:,}")
    print(f"   Bid/Ask: ${candidate.bid:.2f} / ${candidate.ask:.2f}")
    print()

    # Judge the option
    print("Getting Judge verdict...")
    verdict = judge.grade(
        symbol=args.symbol,
        direction=args.direction,
        strike=candidate.strike,
        expiration=candidate.expiration,
        use_llm=True
    )

    print(f"Grade: {verdict.grade.value}-TIER ({verdict.score:.1f}/10)")
    print(f"  Tech: {verdict.technical_score:.1f} | Liq: {verdict.liquidity_score:.1f} | Cat: {verdict.catalyst_score:.1f}")
    print()

    # Check if meets minimum grade
    min_grade = config.scoring.min_trade_grade
    grade_rank = {"A": 3, "B": 2, "N": 1}

    if grade_rank.get(verdict.grade.value, 0) < grade_rank.get(min_grade, 0):
        print(f"❌ BLOCKED: Grade {verdict.grade.value} below minimum {min_grade}")
        print(f"   To execute anyway, set min_trade_grade: 'B' in config")
        return 1

    # Check governor
    if not governor.can_trade():
        print(f"❌ BLOCKED: Risk Governor says no")
        print(f"   Reason: {governor.get_block_reason()}")
        return 1

    # Calculate contracts
    max_risk = config.risk.max_risk_per_trade
    price_per_contract = candidate.ask * 100
    contracts = min(
        int(max_risk / price_per_contract) if price_per_contract > 0 else 1,
        config.risk.max_contracts
    )
    contracts = max(1, contracts)

    print(f"Position Size:")
    print(f"  Contracts: {contracts}")
    print(f"  Entry: ~${candidate.ask:.2f} per contract")
    print(f"  Total cost: ${contracts * price_per_contract:.2f}")
    print(f"  Max risk: ${max_risk:.2f}")
    print()

    # Create Trade Signal (simplified - manual entry)
    signal = TradeSignal(
        id=f"manual_{args.symbol}_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
        ticker=args.symbol,
        direction=args.direction,
        catalyst_type="manual",
        catalyst_description=f"Manual 0DTE {args.direction} execution via GitHub Actions",
        catalyst_time=datetime.now(),
        current_price=price,
        vwap=vwap,
        priority=10
    )

    # Create Trade
    trade = Trade(
        signal=signal,
        grade=verdict.grade,
        contracts=contracts,
        max_risk=max_risk,
        strike=candidate.strike,
        expiration=candidate.expiration
    )
    trade.approve()

    # Execute
    if args.dry_run:
        print("=" * 70)
        print("DRY RUN - Would execute:")
        print(f"  BUY {contracts}x {args.symbol} ${candidate.strike:.0f} {args.direction.upper()} @ {candidate.expiration}")
        print(f"  Entry: ${candidate.ask:.2f} (${contracts * price_per_contract:.2f} total)")
        print(f"  Stop: -50% | Trailing: 25% from HWM")
        print("=" * 70)
        return 0

    if not config.armed:
        print("❌ System NOT ARMED")
        print("   Set 'armed: true' in config/default.yaml to enable live trading")
        return 1

    print("=" * 70)
    print("🚀 EXECUTING TRADE...")
    print("=" * 70)

    position = executor.execute_trade(trade)

    if position:
        print(f"\n✅ TRADE EXECUTED!")
        print(f"   Position ID: {position.id}")
        print(f"   Entry: ${position.entry_price:.2f} x {position.contracts} contracts")
        print(f"   Total: ${position.entry_price * position.contracts * 100:.2f}")
        print(f"\n⚠️  Position is now LIVE - monitor via:")
        print(f"   python engine/monitor_positions.py")
        print()
        return 0
    else:
        print(f"\n❌ TRADE FAILED")
        if trade.rejection_reason:
            print(f"   Reason: {trade.rejection_reason}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
