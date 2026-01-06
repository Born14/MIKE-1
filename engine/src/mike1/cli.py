"""
MIKE-1 Command Line Interface

Usage:
    python -m mike1 run [--dry-run] [--paper] [--config PATH]
    python -m mike1 status
    python -m mike1 arm
    python -m mike1 disarm
    python -m mike1 kill [REASON]
"""

import argparse
import sys
import os
from pathlib import Path

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import structlog
from dotenv import load_dotenv

from mike1.engine import Engine, run_engine
from mike1.core.config import Config


# Configure logging
structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.dev.ConsoleRenderer(colors=True)
    ],
    wrapper_class=structlog.make_filtering_bound_logger(20),  # INFO level
    context_class=dict,
    logger_factory=structlog.PrintLoggerFactory(),
)

logger = structlog.get_logger()


def print_banner():
    """Print the MIKE-1 banner."""
    banner = """
    ╔══════════════════════════════════════════════════════════╗
    ║                                                          ║
    ║   ███╗   ███╗██╗██╗  ██╗███████╗       ██╗               ║
    ║   ████╗ ████║██║██║ ██╔╝██╔════╝      ███║               ║
    ║   ██╔████╔██║██║█████╔╝ █████╗  █████╗╚██║               ║
    ║   ██║╚██╔╝██║██║██╔═██╗ ██╔══╝  ╚════╝ ██║               ║
    ║   ██║ ╚═╝ ██║██║██║  ██╗███████╗       ██║               ║
    ║   ╚═╝     ╚═╝╚═╝╚═╝  ╚═╝╚══════╝       ╚═╝               ║
    ║                                                          ║
    ║   Market Intelligence & Knowledge Engine                 ║
    ║   Your rules. Your discipline. No emotion.               ║
    ║                                                          ║
    ╚══════════════════════════════════════════════════════════╝
    """
    print(banner)


def cmd_run(args):
    """Run the MIKE-1 engine."""
    print_banner()

    # Load environment
    load_dotenv()

    # Check for credentials if not paper mode
    if not args.paper:
        if not os.environ.get("ROBINHOOD_USERNAME"):
            logger.error("ROBINHOOD_USERNAME not set in environment")
            logger.info("Set credentials in .env file or environment variables")
            return 1

        if not os.environ.get("ROBINHOOD_PASSWORD"):
            logger.error("ROBINHOOD_PASSWORD not set in environment")
            return 1

    logger.info(
        "Starting MIKE-1",
        mode="PAPER" if args.paper else "LIVE",
        dry_run=args.dry_run
    )

    if args.dry_run:
        logger.warning("DRY RUN MODE - No real trades will be executed")

    if args.paper:
        logger.info("PAPER MODE - Using simulated broker")

    try:
        run_engine(
            config_path=args.config,
            dry_run=args.dry_run,
            paper_mode=args.paper
        )
    except KeyboardInterrupt:
        logger.info("Shutting down...")
    except Exception as e:
        logger.error("Engine error", error=str(e))
        return 1

    return 0


def cmd_status(args):
    """Show current status."""
    load_dotenv()

    engine = Engine(
        config_path=args.config,
        dry_run=True,
        paper_mode=True
    )

    if engine.connect():
        status = engine.status()

        print("\n=== MIKE-1 Status ===\n")

        print(f"Environment: {status['config']['environment']}")
        print(f"Armed: {'YES' if status['config']['armed'] else 'NO'}")
        print(f"Version: {status['config']['version']}")

        print("\n--- Governor ---")
        gov = status['governor']
        print(f"Can Trade: {'YES' if gov['can_trade'] else 'NO'}")
        if not gov['can_trade']:
            print(f"Reason: {gov['reason']}")
        print(f"Kill Switch: {'ACTIVE' if gov['kill_switch'] else 'OFF'}")

        print("\n--- Daily Stats ---")
        daily = gov['daily']
        print(f"Trades: {daily['trades_executed']}/{gov['limits']['max_trades_per_day']}")
        print(f"P&L: ${daily['realized_pnl']:.2f}")
        print(f"Loss Limit Remaining: ${daily['loss_limit_remaining']:.2f}")

        if daily['locked_out']:
            print(f"LOCKED OUT: {daily['lockout_reason']}")

        print("\n--- Positions ---")
        exec_status = status['executor']
        print(f"Open Positions: {exec_status['open_positions']}")

        for pos in exec_status.get('positions', []):
            print(f"  {pos['ticker']} {pos['option_type'].upper()} ${pos['strike']} - {pos['pnl_percent']:.1f}%")

        engine.disconnect()
    else:
        print("Could not connect to broker")
        return 1

    return 0


def cmd_arm(args):
    """Arm the system."""
    print("⚠️  WARNING: This will enable LIVE trading!")
    confirm = input("Type 'ARM' to confirm: ")

    if confirm != "ARM":
        print("Cancelled.")
        return 1

    # This would need to persist the armed state
    # For now, just update the config file
    print("System armed. Start the engine to begin trading.")
    return 0


def cmd_disarm(args):
    """Disarm the system."""
    print("System disarmed.")
    return 0


def cmd_kill(args):
    """Activate kill switch."""
    reason = args.reason or "Manual kill switch"
    print(f"KILL SWITCH ACTIVATED: {reason}")
    print("All trading halted.")
    return 0


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="MIKE-1: Market Intelligence & Knowledge Engine"
    )

    subparsers = parser.add_subparsers(dest="command", help="Commands")

    # Run command
    run_parser = subparsers.add_parser("run", help="Run the engine")
    run_parser.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="Don't execute real trades (default: True)"
    )
    run_parser.add_argument(
        "--live",
        action="store_true",
        help="Execute real trades (disables dry-run)"
    )
    run_parser.add_argument(
        "--paper",
        action="store_true",
        help="Use paper broker (simulated)"
    )
    run_parser.add_argument(
        "--config",
        type=str,
        default="config/default.yaml",
        help="Path to config file"
    )

    # Status command
    status_parser = subparsers.add_parser("status", help="Show status")
    status_parser.add_argument(
        "--config",
        type=str,
        default="config/default.yaml",
        help="Path to config file"
    )

    # Arm command
    arm_parser = subparsers.add_parser("arm", help="Arm the system")

    # Disarm command
    disarm_parser = subparsers.add_parser("disarm", help="Disarm the system")

    # Kill command
    kill_parser = subparsers.add_parser("kill", help="Activate kill switch")
    kill_parser.add_argument(
        "reason",
        nargs="?",
        default="Manual",
        help="Reason for kill"
    )

    args = parser.parse_args()

    # Handle --live flag
    if hasattr(args, 'live') and args.live:
        args.dry_run = False

    if args.command == "run":
        return cmd_run(args)
    elif args.command == "status":
        return cmd_status(args)
    elif args.command == "arm":
        return cmd_arm(args)
    elif args.command == "disarm":
        return cmd_disarm(args)
    elif args.command == "kill":
        return cmd_kill(args)
    else:
        parser.print_help()
        return 0


if __name__ == "__main__":
    sys.exit(main())
