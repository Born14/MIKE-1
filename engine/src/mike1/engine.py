"""
MIKE-1 Engine

The main execution loop that ties everything together.
"""

import os
import time
import signal
import sys
from datetime import datetime
from typing import Optional
import structlog

from .core.config import Config, get_config
from .core.risk_governor import RiskGovernor
from .modules.executor import Executor
from .modules.broker import Broker, PaperBroker
from .modules.broker_alpaca import AlpacaBroker
from .modules.broker_factory import BrokerFactory
from .modules.logger import TradeLogger


logger = structlog.get_logger()


class Engine:
    """
    The MIKE-1 Engine.

    Orchestrates all modules:
    - Broker connection
    - Position monitoring
    - Exit enforcement
    - Logging

    This is the main entry point for running MIKE-1.
    """

    def __init__(
        self,
        config_path: Optional[str] = None,
        dry_run: bool = True,
        use_alpaca: bool = True
    ):
        # Load configuration
        self.config = Config.load(config_path) if config_path else get_config()
        self.config_path = config_path

        # Operating modes
        self.dry_run = dry_run
        self.use_alpaca = use_alpaca

        # Initialize broker
        if use_alpaca:
            api_key = os.getenv("ALPACA_API_KEY")
            secret_key = os.getenv("ALPACA_SECRET_KEY")
            paper = os.getenv("ALPACA_PAPER", "true").lower() == "true"

            if not api_key or not secret_key:
                logger.warning("Alpaca credentials not found, falling back to Paper Broker")
                self.broker = PaperBroker()
            else:
                self.broker = AlpacaBroker(api_key, secret_key, paper=paper)
                logger.info("Using Alpaca Broker", paper=paper)
        else:
            self.broker = PaperBroker()
            logger.info("Using Paper Broker (simulated)")

        # Initialize components
        self.governor = RiskGovernor(self.config)
        self.executor = Executor(
            broker=self.broker,
            config=self.config,
            risk_governor=self.governor,
            dry_run=dry_run
        )
        self.logger = TradeLogger()

        # State
        self.running = False
        self.last_config_check = datetime.now()

        # Setup signal handlers
        signal.signal(signal.SIGINT, self._handle_shutdown)
        signal.signal(signal.SIGTERM, self._handle_shutdown)

        logger.info(
            "MIKE-1 Engine initialized",
            dry_run=dry_run,
            use_alpaca=use_alpaca,
            environment=self.config.environment,
            armed=self.config.armed
        )

    def _handle_shutdown(self, signum, frame):
        """Handle shutdown signals gracefully."""
        logger.info("Shutdown signal received")
        self.stop()
        sys.exit(0)

    def _check_config_reload(self) -> None:
        """Check if config should be reloaded (hot reload)."""
        # Reload config every 60 seconds
        if (datetime.now() - self.last_config_check).seconds > 60:
            if self.config_path:
                try:
                    new_config = Config.load(self.config_path)
                    self.config = new_config
                    self.governor.config = new_config
                    self.executor.config = new_config
                    logger.info("Configuration reloaded")
                except Exception as e:
                    logger.error("Failed to reload config", error=str(e))

            self.last_config_check = datetime.now()

    def connect(self) -> bool:
        """Connect to the broker."""
        logger.info("Connecting to broker...")

        if not self.broker.connect():
            logger.error("Failed to connect to broker")
            return False

        logger.info("Broker connected")

        # Log account info
        account = self.broker.get_account_info()
        if account:
            logger.info(
                "Account status",
                buying_power=f"${account.get('buying_power', 0):.2f}",
                portfolio_value=f"${account.get('portfolio_value', 0):.2f}"
            )

        return True

    def disconnect(self) -> None:
        """Disconnect from the broker."""
        self.broker.disconnect()
        logger.info("Broker disconnected")

    def start(self) -> None:
        """
        Start the engine main loop.

        This runs continuously, polling for position updates
        and enforcing exit rules.
        """
        if not self.broker.connected:
            if not self.connect():
                logger.error("Cannot start - broker not connected")
                return

        self.running = True
        poll_interval = self.config.engine.poll_interval

        logger.info(
            "MIKE-1 Engine starting",
            poll_interval=f"{poll_interval}s",
            dry_run=self.dry_run
        )

        self.logger.log_system_event("engine_start", {
            "dry_run": self.dry_run,
            "use_alpaca": self.use_alpaca,
            "config_version": self.config.version
        })

        while self.running:
            try:
                self._poll_cycle()
                time.sleep(poll_interval)

            except KeyboardInterrupt:
                logger.info("Keyboard interrupt received")
                break

            except Exception as e:
                logger.error("Error in main loop", error=str(e))
                self.logger.log_system_event("error", {"error": str(e)})
                time.sleep(poll_interval)

        self.stop()

    def _poll_cycle(self) -> None:
        """Single poll cycle."""
        # Check for config changes
        self._check_config_reload()

        # Check if trading is allowed
        can_trade, reason = self.governor.can_trade()

        if not can_trade and "not armed" not in reason.lower():
            logger.warning("Trading blocked", reason=reason)

        # Run executor poll
        actions = self.executor.poll()

        # Log any actions taken
        for action in actions:
            self.logger.log_action(
                action_type=action.get("type"),
                position_id=action.get("position_id"),
                ticker=action.get("ticker"),
                details=action,
                dry_run=self.dry_run
            )

    def stop(self) -> None:
        """Stop the engine."""
        self.running = False
        logger.info("MIKE-1 Engine stopping")

        self.logger.log_system_event("engine_stop", {
            "governor_status": self.governor.get_status()
        })

        self.disconnect()

    def status(self) -> dict:
        """Get current engine status."""
        return {
            "running": self.running,
            "dry_run": self.dry_run,
            "use_alpaca": self.use_alpaca,
            "broker_connected": self.broker.connected,
            "config": {
                "version": self.config.version,
                "environment": self.config.environment,
                "armed": self.config.armed,
            },
            "governor": self.governor.get_status(),
            "executor": self.executor.get_status(),
        }

    def arm(self) -> None:
        """Arm the system for live trading."""
        if self.dry_run:
            logger.warning("Cannot arm in dry run mode")
            return

        self.config.armed = True
        logger.warning("SYSTEM ARMED - Live trading enabled")
        self.logger.log_system_event("armed")

    def disarm(self) -> None:
        """Disarm the system."""
        self.config.armed = False
        logger.info("System disarmed")
        self.logger.log_system_event("disarmed")

    def kill(self, reason: str = "Manual kill") -> None:
        """Activate kill switch."""
        self.governor.activate_kill_switch(reason)
        self.logger.log_governor_event("kill_switch_activated", {"reason": reason})


def run_engine(
    config_path: Optional[str] = None,
    dry_run: bool = True,
    use_alpaca: bool = True
) -> None:
    """
    Convenience function to run the engine.

    Args:
        config_path: Path to config file
        dry_run: If True, don't execute real trades
        use_alpaca: If True, use Alpaca broker
    """
    engine = Engine(
        config_path=config_path,
        dry_run=dry_run,
        use_alpaca=use_alpaca
    )

    try:
        engine.start()
    except Exception as e:
        logger.error("Engine crashed", error=str(e))
        engine.stop()
        raise


# =============================================================================
# COMMAND LINE ENTRY POINT
# =============================================================================

def main():
    """Command line entry point for MIKE-1 engine."""
    import argparse
    from dotenv import load_dotenv

    # Load environment
    load_dotenv()

    parser = argparse.ArgumentParser(description="MIKE-1 Trading Engine")
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # Monitor Command (Default)
    monitor_parser = subparsers.add_parser("monitor", help="Start the monitoring engine")
    monitor_parser.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="Run without executing real trades (default: True)"
    )
    monitor_parser.add_argument(
        "--live",
        action="store_true",
        help="Execute real trades (disables dry-run)"
    )
    monitor_parser.add_argument(
        "--paper-broker",
        action="store_true",
        help="Use local paper broker instead of Alpaca"
    )
    monitor_parser.add_argument(
        "--config",
        type=str,
        help="Path to config file"
    )
    monitor_parser.add_argument(
        "-y", "--yes",
        action="store_true",
        help="Skip confirmation prompt for live trading"
    )

    # Grade Command
    grade_parser = subparsers.add_parser("grade", help="Grade a trade candidate")
    grade_parser.add_argument("symbol", type=str, help="Ticker symbol (e.g. NVDA)")
    grade_parser.add_argument("direction", type=str, choices=["call", "put"], help="Trade direction")
    grade_parser.add_argument("--strike", type=float, help="Option strike price")
    grade_parser.add_argument("--expiration", type=str, help="Option expiration (YYYY-MM-DD)")
    grade_parser.add_argument(
        "--paper-broker", 
        action="store_true", 
        help="Use local paper broker"
    )

    args = parser.parse_args()

    # Default to monitor if no command provided (backward compatibility)
    if not args.command:
        args.command = "monitor"
        # Manually set defaults for monitor args since they weren't parsed
        if not hasattr(args, 'live'): args.live = False
        if not hasattr(args, 'paper_broker'): args.paper_broker = False
        if not hasattr(args, 'config'): args.config = None

    if args.command == "monitor":
        # Determine dry_run mode
        dry_run = not args.live

        print("=" * 60)
        print("MIKE-1 TRADING ENGINE")
        print("=" * 60)
        print()
        print(f"  Mode: {'DRY RUN' if dry_run else 'LIVE TRADING'}")
        print(f"  Broker: {'Paper (local)' if args.paper_broker else 'Alpaca'}")
        print()

        if not dry_run and not getattr(args, 'yes', False):
            print("  WARNING: Live trading enabled!")
            print("  Trades WILL be executed.")
            print()
            confirm = input("  Type 'CONFIRM' to continue: ")
            if confirm != "CONFIRM":
                print("  Aborted.")
                return
        elif not dry_run:
            print("  WARNING: Live trading enabled! (--yes flag used)")
            print()

        run_engine(
            config_path=args.config,
            dry_run=dry_run,
            use_alpaca=not args.paper_broker
        )

    elif args.command == "grade":
        from .modules.judge import Judge
        from .modules.broker_alpaca import AlpacaBroker
        from .modules.broker import PaperBroker
        from .modules.llm_client import get_llm_client

        print(f"Grading {args.symbol} {args.direction.upper()}...")

        # Setup Broker
        if args.paper_broker:
            broker = PaperBroker()
        else:
            api_key = os.getenv("ALPACA_API_KEY")
            secret_key = os.getenv("ALPACA_SECRET_KEY")
            paper = os.getenv("ALPACA_PAPER", "true").lower() == "true"
            if not api_key:
                print("Error: ALPACA_API_KEY not found.")
                return
            broker = AlpacaBroker(api_key, secret_key, paper=paper)
            broker.connect()

        # Setup Judge with LLM client (if API key configured)
        llm_client = get_llm_client()
        judge = Judge(broker, llm_client=llm_client)

        # Run Grading
        verdict = judge.grade(
            args.symbol.upper(),
            args.direction,
            strike=args.strike,
            expiration=args.expiration
        )

        # Output Report
        print("\n" + "="*50)
        print(judge.explain(verdict))
        print("="*50 + "\n")

if __name__ == "__main__":
    main()
