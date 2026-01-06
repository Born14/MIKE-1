"""
Broker Factory for MIKE-1

Creates the appropriate broker based on configuration.
"""

from typing import Optional
import structlog

from .broker import Broker, PaperBroker
from .broker_alpaca import AlpacaBroker

logger = structlog.get_logger()


class BrokerFactory:
    """
    Factory for creating broker instances.

    Supports:
    - paper: Simulated trading (no real money)
    - alpaca: Alpaca Markets (official API, recommended)
    """

    @staticmethod
    def create(broker_type: str, **kwargs) -> Broker:
        """
        Create a broker instance.

        Args:
            broker_type: One of 'paper', 'alpaca'
            **kwargs: Broker-specific configuration

        Returns:
            Broker instance
        """
        broker_type = broker_type.lower()

        if broker_type == "paper":
            logger.info("Creating Paper Broker")
            starting_cash = kwargs.get("starting_cash", 10000.0)
            return PaperBroker(starting_cash=starting_cash)

        elif broker_type == "alpaca":
            logger.info("Creating Alpaca Broker")
            return AlpacaBroker(
                api_key=kwargs.get("api_key"),
                secret_key=kwargs.get("secret_key"),
                paper=kwargs.get("paper", True)
            )

        else:
            raise ValueError(f"Unknown broker type: {broker_type}. Supported: 'paper', 'alpaca'")

    @staticmethod
    def create_with_failover(
        primary: str,
        fallback: str = "paper",
        **kwargs
    ) -> "FailoverBroker":
        """
        Create a broker with automatic failover.

        Args:
            primary: Primary broker type ('alpaca')
            fallback: Fallback broker type if primary fails ('paper')
            **kwargs: Broker configuration

        Returns:
            FailoverBroker instance
        """
        primary_broker = BrokerFactory.create(primary, **kwargs)
        fallback_broker = BrokerFactory.create(fallback, **kwargs)

        return FailoverBroker(primary_broker, fallback_broker)


class FailoverBroker(Broker):
    """
    Broker that automatically fails over to a backup.

    If the primary broker fails to connect or errors out,
    automatically switches to the fallback broker.
    """

    def __init__(self, primary: Broker, fallback: Broker):
        self.primary = primary
        self.fallback = fallback
        self.active: Optional[Broker] = None
        self.using_fallback = False

    def connect(self) -> bool:
        """Connect to primary, fall back if it fails."""
        logger.info("Attempting primary broker connection...")

        if self.primary.connect():
            self.active = self.primary
            self.using_fallback = False
            logger.info("Connected to primary broker")
            return True

        logger.warning("Primary broker failed, trying fallback...")

        if self.fallback.connect():
            self.active = self.fallback
            self.using_fallback = True
            logger.warning("Connected to fallback broker")
            return True

        logger.error("All brokers failed to connect")
        return False

    def disconnect(self) -> None:
        """Disconnect from active broker."""
        if self.active:
            self.active.disconnect()
        self.active = None

    def _ensure_connected(self) -> bool:
        """Ensure we have an active connection."""
        if self.active and self.active.connected:
            return True
        return self.connect()

    def get_account_info(self) -> dict:
        if not self._ensure_connected():
            return {}
        return self.active.get_account_info()

    def get_option_positions(self):
        if not self._ensure_connected():
            return []
        return self.active.get_option_positions()

    def get_option_quote(self, symbol, strike, expiration, option_type):
        if not self._ensure_connected():
            return None
        return self.active.get_option_quote(symbol, strike, expiration, option_type)

    def get_option_chain(self, symbol, expiration, option_type):
        if not self._ensure_connected():
            return []
        return self.active.get_option_chain(symbol, expiration, option_type)

    def buy_option(self, symbol, strike, expiration, option_type, quantity, price=None):
        if not self._ensure_connected():
            from .broker import OrderResult
            return OrderResult(success=False, message="Not connected")
        return self.active.buy_option(symbol, strike, expiration, option_type, quantity, price)

    def sell_option(self, symbol, strike, expiration, option_type, quantity, price=None):
        if not self._ensure_connected():
            from .broker import OrderResult
            return OrderResult(success=False, message="Not connected")
        return self.active.sell_option(symbol, strike, expiration, option_type, quantity, price)

    def get_stock_price(self, symbol):
        if not self._ensure_connected():
            return 0
        return self.active.get_stock_price(symbol)

    @property
    def connected(self) -> bool:
        return self.active is not None and self.active.connected

    def get_status(self) -> dict:
        """Get failover status."""
        return {
            "connected": self.connected,
            "using_fallback": self.using_fallback,
            "active_broker": type(self.active).__name__ if self.active else None,
            "primary_broker": type(self.primary).__name__,
            "fallback_broker": type(self.fallback).__name__,
        }
