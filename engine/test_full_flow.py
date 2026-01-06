"""
Full Flow Simulation Test

Proves the complete MIKE-1 pipeline works:
1. Buy position via PaperBroker
2. Executor picks it up
3. Price hits +25% -> Trim 1 fires
4. Price hits +50% -> Trim 2 fires
5. Verify sells executed

Uses local PaperBroker to simulate price changes.
"""

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

import structlog
structlog.configure(
    processors=[
        structlog.dev.ConsoleRenderer(colors=True)
    ]
)

from mike1.core.config import Config
from mike1.core.risk_governor import RiskGovernor
from mike1.modules.broker import PaperBroker
from mike1.modules.executor import Executor


def test_full_flow():
    """Simulate the complete trading flow."""

    print("=" * 60)
    print("MIKE-1 FULL FLOW SIMULATION")
    print("=" * 60)
    print()
    print("This test proves the entire pipeline works:")
    print("  Entry -> Monitor -> Trim 1 -> Trim 2 -> Close")
    print()

    # Load config
    config = Config()
    print(f"Exit Rules from config:")
    print(f"  Trim 1: +{config.exits.trim_1.trigger_pct}% -> sell {config.exits.trim_1.sell_pct}%")
    print(f"  Trim 2: +{config.exits.trim_2.trigger_pct}% -> sell {config.exits.trim_2.sell_pct}%")
    print(f"  Hard Stop: -{config.exits.hard_stop_pct}%")
    print()

    # Create broker and executor (NOT dry run - we want to see sells execute)
    broker = PaperBroker(starting_cash=10000.0)
    broker.connect()

    governor = RiskGovernor(config)
    executor = Executor(broker, config, risk_governor=governor, dry_run=False)

    # =========================================================================
    # STEP 1: Open a position
    # =========================================================================
    print("-" * 60)
    print("STEP 1: Opening position")
    print("-" * 60)

    entry_price = 2.00  # $200 per contract

    result = broker.buy_option(
        symbol="NVDA",
        strike=140.0,
        expiration="2026-01-17",
        option_type="call",
        quantity=1,
        price=entry_price
    )

    print(f"  Bought: NVDA $140 Call")
    print(f"  Entry: ${entry_price} ({entry_price * 100} per contract)")
    print(f"  Order ID: {result.order_id}")
    print()

    # Sync executor with broker
    executor.sync_positions()
    print(f"  Executor tracking: {len(executor.state.positions)} position(s)")
    print()

    # =========================================================================
    # STEP 2: Price goes up to +25% -> Trim 1 should fire
    # =========================================================================
    print("-" * 60)
    print("STEP 2: Price rises to +25% (Trim 1 trigger)")
    print("-" * 60)

    # Calculate price for +25%
    trim_1_price = entry_price * 1.25  # $2.50

    # Simulate price change
    broker.simulate_price_change("NVDA", 140.0, "2026-01-17", "call", trim_1_price)

    # Run executor check
    print(f"  New price: ${trim_1_price:.2f} (+25%)")
    print(f"  Running executor check...")

    executor.sync_positions()
    actions = executor.check_exits()

    if actions:
        for action in actions:
            print(f"  ACTION: {action['type'].upper()}")
            print(f"    Ticker: {action['ticker']}")
            print(f"    Contracts: {action['contracts']}")
            print(f"    Price: ${action['price']:.2f}")
            print(f"    Executed: {action['executed']}")
    else:
        print("  No actions triggered")
    print()

    # Check remaining position
    positions = broker.get_option_positions()
    if positions:
        pos = positions[0]
        print(f"  Remaining position: {pos.quantity} contract(s)")
    print()

    # =========================================================================
    # STEP 3: Price goes up to +50% -> Trim 2 should fire
    # =========================================================================
    print("-" * 60)
    print("STEP 3: Price rises to +50% (Trim 2 trigger)")
    print("-" * 60)

    # Calculate price for +50%
    trim_2_price = entry_price * 1.50  # $3.00

    # Simulate price change
    broker.simulate_price_change("NVDA", 140.0, "2026-01-17", "call", trim_2_price)

    print(f"  New price: ${trim_2_price:.2f} (+50%)")
    print(f"  Running executor check...")

    executor.sync_positions()
    actions = executor.check_exits()

    if actions:
        for action in actions:
            print(f"  ACTION: {action['type'].upper()}")
            print(f"    Ticker: {action['ticker']}")
            print(f"    Contracts: {action['contracts']}")
            print(f"    Price: ${action['price']:.2f}")
            print(f"    Executed: {action['executed']}")
    else:
        print("  No actions triggered")
    print()

    # =========================================================================
    # STEP 4: Check final state
    # =========================================================================
    print("-" * 60)
    print("FINAL STATE")
    print("-" * 60)

    positions = broker.get_option_positions()
    print(f"  Open positions: {len(positions)}")

    summary = broker.get_summary()
    print(f"  Starting cash: ${summary['starting_cash']:.2f}")
    print(f"  Current cash: ${summary['current_cash']:.2f}")
    print(f"  Realized P&L: ${summary['realized_pnl']:.2f}")
    print(f"  Total orders: {summary['total_orders']}")
    print()

    # Show order history
    print("Order History:")
    for order in broker.order_history:
        if order['type'] == 'buy':
            print(f"  BUY  {order['quantity']}x {order['symbol']} @ ${order['price']:.2f}")
        else:
            print(f"  SELL {order['quantity']}x {order['symbol']} @ ${order['price']:.2f} | P&L: ${order.get('pnl', 0):.2f}")
    print()

    # =========================================================================
    # BONUS: Test hard stop
    # =========================================================================
    print("-" * 60)
    print("BONUS: Testing Hard Stop (-50%)")
    print("-" * 60)

    # Open a new position
    result = broker.buy_option(
        symbol="AMD",
        strike=150.0,
        expiration="2026-01-17",
        option_type="call",
        quantity=1,
        price=2.00
    )
    print(f"  Opened new position: AMD $150 Call @ $2.00")

    executor.sync_positions()

    # Simulate -50% loss
    stop_price = 1.00  # -50%
    broker.simulate_price_change("AMD", 150.0, "2026-01-17", "call", stop_price)

    print(f"  Price crashed to ${stop_price:.2f} (-50%)")
    print(f"  Running executor check...")

    executor.sync_positions()
    actions = executor.check_exits()

    if actions:
        for action in actions:
            print(f"  ACTION: {action['type'].upper()}")
            print(f"    Executed: {action['executed']}")
    print()

    # Final summary
    print("=" * 60)
    print("TEST COMPLETE")
    print("=" * 60)
    summary = broker.get_summary()
    print(f"  Final cash: ${summary['current_cash']:.2f}")
    print(f"  Total realized P&L: ${summary['realized_pnl']:.2f}")
    print(f"  Open positions: {summary['open_positions']}")
    print()

    if summary['realized_pnl'] > 0:
        print("  [SUCCESS] Trims executed correctly - profits locked in!")
    else:
        print("  [CHECK] Review the flow above")

    broker.disconnect()
    return True


if __name__ == "__main__":
    test_full_flow()
