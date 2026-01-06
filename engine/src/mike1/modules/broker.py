"""
Broker Integration for MIKE-1

Base broker interface and Paper broker for testing.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Optional
import structlog

logger = structlog.get_logger()


@dataclass
class OptionQuote:
    """Current option quote data."""
    symbol: str
    strike: float
    expiration: str
    option_type: str  # call or put

    bid: float
    ask: float
    mark: float  # mid price
    last: float

    volume: int
    open_interest: int
    implied_volatility: float
    delta: float
    gamma: float
    theta: float
    vega: float

    underlying_price: float


@dataclass
class OptionPosition:
    """Current option position from broker."""
    id: str
    symbol: str
    option_type: str
    strike: float
    expiration: str

    quantity: float
    average_cost: float
    current_price: float

    created_at: Optional[datetime] = None


@dataclass
class OrderResult:
    """Result of an order execution."""
    success: bool
    order_id: Optional[str] = None
    filled_quantity: float = 0
    filled_price: float = 0
    message: str = ""
    timestamp: datetime = None

    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.now()


class Broker(ABC):
    """Abstract base class for broker integrations."""

    @abstractmethod
    def connect(self) -> bool:
        """Connect to the broker. Returns True if successful."""
        pass

    @abstractmethod
    def disconnect(self) -> None:
        """Disconnect from the broker."""
        pass

    @abstractmethod
    def get_account_info(self) -> dict:
        """Get account information (buying power, etc.)."""
        pass

    @abstractmethod
    def get_option_positions(self) -> list[OptionPosition]:
        """Get all current option positions."""
        pass

    @abstractmethod
    def get_option_quote(
        self,
        symbol: str,
        strike: float,
        expiration: str,
        option_type: str
    ) -> Optional[OptionQuote]:
        """Get current quote for a specific option."""
        pass

    @abstractmethod
    def get_option_chain(
        self,
        symbol: str,
        expiration: str,
        option_type: str
    ) -> list[OptionQuote]:
        """Get option chain for a symbol."""
        pass

    @abstractmethod
    def buy_option(
        self,
        symbol: str,
        strike: float,
        expiration: str,
        option_type: str,
        quantity: int,
        price: Optional[float] = None  # None = market order
    ) -> OrderResult:
        """Buy an option contract."""
        pass

    @abstractmethod
    def sell_option(
        self,
        symbol: str,
        strike: float,
        expiration: str,
        option_type: str,
        quantity: int,
        price: Optional[float] = None
    ) -> OrderResult:
        """Sell an option contract."""
        pass

    @abstractmethod
    def get_stock_price(self, symbol: str) -> float:
        """Get current stock price."""
        pass

    def get_volume_data(self, symbol: str) -> Optional[dict]:
        """Get volume data (current and average)."""
        return None

    def get_vwap(self, symbol: str) -> Optional[dict]:
        """Get VWAP data."""
        return None

    def get_rsi(self, symbol: str, period: int = 14) -> float:
        """Get RSI."""
        return 50.0

    def get_news(self, symbol: str, limit: int = 5) -> list[dict]:
        """Get recent news."""
        return []

    def get_atr(self, symbol: str, period: int = 14) -> float:
        """
        Get ATR (Average True Range) for a symbol.

        Default implementation returns 0 - override in subclasses.
        """
        return 0


class PaperBroker(Broker):
    """
    Paper trading broker for testing.

    Simulates order execution without real money.
    Use this for testing MIKE-1 before going live.
    """

    def __init__(self, starting_cash: float = 10000.0):
        self.connected = False
        self.positions: list[OptionPosition] = []
        self.cash = starting_cash
        self.starting_cash = starting_cash
        self.order_id_counter = 0
        self.order_history: list[dict] = []

    def connect(self) -> bool:
        self.connected = True
        logger.info("Connected to Paper Broker", starting_cash=self.cash)
        return True

    def disconnect(self) -> None:
        self.connected = False
        logger.info("Disconnected from Paper Broker")

    def get_account_info(self) -> dict:
        # Calculate portfolio value
        position_value = sum(
            p.current_price * p.quantity * 100
            for p in self.positions
        )

        return {
            "buying_power": self.cash,
            "cash": self.cash,
            "portfolio_value": self.cash + position_value,
            "equity": self.cash + position_value,
            "starting_cash": self.starting_cash,
            "positions_count": len(self.positions),
        }

    def get_option_positions(self) -> list[OptionPosition]:
        return self.positions

    def get_option_quote(
        self,
        symbol: str,
        strike: float,
        expiration: str,
        option_type: str
    ) -> Optional[OptionQuote]:
        """Return simulated quote based on position or defaults."""
        # Check if we have a position for this option
        for pos in self.positions:
            if (pos.symbol == symbol and
                pos.strike == strike and
                pos.expiration == expiration and
                pos.option_type == option_type):
                # Return quote based on position's current price
                return OptionQuote(
                    symbol=symbol,
                    strike=strike,
                    expiration=expiration,
                    option_type=option_type,
                    bid=pos.current_price * 0.98,
                    ask=pos.current_price * 1.02,
                    mark=pos.current_price,
                    last=pos.current_price,
                    volume=1000,
                    open_interest=5000,
                    implied_volatility=0.30,
                    delta=0.35 if option_type == "call" else -0.35,
                    gamma=0.05,
                    theta=-0.10,
                    vega=0.15,
                    underlying_price=100.0,
                )

        # Default simulated quote
        return OptionQuote(
            symbol=symbol,
            strike=strike,
            expiration=expiration,
            option_type=option_type,
            bid=1.00,
            ask=1.10,
            mark=1.05,
            last=1.05,
            volume=1000,
            open_interest=5000,
            implied_volatility=0.30,
            delta=0.35 if option_type == "call" else -0.35,
            gamma=0.05,
            theta=-0.10,
            vega=0.15,
            underlying_price=100.0,
        )

    def get_option_chain(
        self,
        symbol: str,
        expiration: str,
        option_type: str
    ) -> list[OptionQuote]:
        """Return simulated option chain."""
        # Return a few simulated strikes around $100
        strikes = [95, 97.5, 100, 102.5, 105]
        chain = []

        for strike in strikes:
            # Simulate pricing based on moneyness
            if option_type == "call":
                intrinsic = max(0, 100 - strike)
                delta = 0.5 + (100 - strike) * 0.02
            else:
                intrinsic = max(0, strike - 100)
                delta = -(0.5 + (strike - 100) * 0.02)

            delta = max(-0.99, min(0.99, delta))
            price = intrinsic + 1.50  # Add time value

            chain.append(OptionQuote(
                symbol=symbol,
                strike=strike,
                expiration=expiration,
                option_type=option_type,
                bid=price * 0.98,
                ask=price * 1.02,
                mark=price,
                last=price,
                volume=500 + int(abs(100 - strike) * 100),
                open_interest=2000 + int(abs(100 - strike) * 200),
                implied_volatility=0.30,
                delta=delta,
                gamma=0.05,
                theta=-0.08,
                vega=0.12,
                underlying_price=100.0,
            ))

        return chain

    def buy_option(
        self,
        symbol: str,
        strike: float,
        expiration: str,
        option_type: str,
        quantity: int,
        price: Optional[float] = None
    ) -> OrderResult:
        self.order_id_counter += 1
        fill_price = price or 1.05

        # Calculate cost
        cost = fill_price * quantity * 100

        if cost > self.cash:
            logger.warning(
                "[PAPER] Insufficient funds",
                required=cost,
                available=self.cash
            )
            return OrderResult(
                success=False,
                message=f"Insufficient funds: need ${cost:.2f}, have ${self.cash:.2f}"
            )

        # Deduct cash
        self.cash -= cost

        # Add position
        position = OptionPosition(
            id=f"PAPER-{self.order_id_counter}",
            symbol=symbol,
            option_type=option_type,
            strike=strike,
            expiration=expiration,
            quantity=quantity,
            average_cost=fill_price,
            current_price=fill_price,
            created_at=datetime.now(),
        )
        self.positions.append(position)

        # Record order
        self.order_history.append({
            "id": position.id,
            "type": "buy",
            "symbol": symbol,
            "strike": strike,
            "expiration": expiration,
            "option_type": option_type,
            "quantity": quantity,
            "price": fill_price,
            "cost": cost,
            "timestamp": datetime.now(),
        })

        logger.info(
            "[PAPER] Buy order filled",
            symbol=symbol,
            strike=strike,
            quantity=quantity,
            price=fill_price,
            cost=cost,
            cash_remaining=self.cash
        )

        return OrderResult(
            success=True,
            order_id=position.id,
            filled_quantity=quantity,
            filled_price=fill_price,
            message="Paper order filled"
        )

    def sell_option(
        self,
        symbol: str,
        strike: float,
        expiration: str,
        option_type: str,
        quantity: int,
        price: Optional[float] = None
    ) -> OrderResult:
        self.order_id_counter += 1

        # Find matching position
        matching_pos = None
        for pos in self.positions:
            if (pos.symbol == symbol and
                pos.strike == strike and
                pos.expiration == expiration and
                pos.option_type == option_type):
                matching_pos = pos
                break

        if not matching_pos:
            return OrderResult(
                success=False,
                message="No matching position found"
            )

        if quantity > matching_pos.quantity:
            return OrderResult(
                success=False,
                message=f"Insufficient quantity: have {matching_pos.quantity}, trying to sell {quantity}"
            )

        fill_price = price or matching_pos.current_price

        # Calculate proceeds
        proceeds = fill_price * quantity * 100
        self.cash += proceeds

        # Calculate P&L
        cost_basis = matching_pos.average_cost * quantity * 100
        pnl = proceeds - cost_basis

        # Update or remove position
        matching_pos.quantity -= quantity
        if matching_pos.quantity <= 0:
            self.positions.remove(matching_pos)

        # Record order
        order_id = f"PAPER-{self.order_id_counter}"
        self.order_history.append({
            "id": order_id,
            "type": "sell",
            "symbol": symbol,
            "strike": strike,
            "expiration": expiration,
            "option_type": option_type,
            "quantity": quantity,
            "price": fill_price,
            "proceeds": proceeds,
            "pnl": pnl,
            "timestamp": datetime.now(),
        })

        logger.info(
            "[PAPER] Sell order filled",
            symbol=symbol,
            strike=strike,
            quantity=quantity,
            price=fill_price,
            proceeds=proceeds,
            pnl=pnl,
            cash_remaining=self.cash
        )

        return OrderResult(
            success=True,
            order_id=order_id,
            filled_quantity=quantity,
            filled_price=fill_price,
            message=f"Paper order filled. P&L: ${pnl:.2f}"
        )

    def get_stock_price(self, symbol: str) -> float:
        """Return simulated stock price."""
        return 100.0

    def get_volume_data(self, symbol: str) -> Optional[dict]:
        """Return simulated volume data."""
        return {
            "current_volume": 1500000,
            "avg_volume": 1000000,
            "timestamp": datetime.now()
        }

    def get_vwap(self, symbol: str) -> Optional[dict]:
        """Return simulated VWAP."""
        return {
            "vwap": 99.50,
            "timestamp": datetime.now()
        }

    def get_rsi(self, symbol: str, period: int = 14) -> float:
        """Return simulated RSI."""
        return 55.0

    def get_news(self, symbol: str, limit: int = 5) -> list[dict]:
        """Return dummy news."""
        return [
            {
                "headline": f"{symbol} beats earnings estimates",
                "summary": "Company reported strong growth...",
                "url": "http://example.com",
                "timestamp": datetime.now()
            }
        ]

    def get_atr(self, symbol: str, period: int = 14) -> float:
        """Return simulated ATR (roughly 2% of stock price)."""
        # Typical ATR is ~2% of stock price for most liquid stocks
        return self.get_stock_price(symbol) * 0.02

    def simulate_price_change(self, symbol: str, strike: float, expiration: str, option_type: str, new_price: float) -> None:
        """
        Simulate a price change for testing.

        Use this to test trim/stop logic.
        """
        for pos in self.positions:
            if (pos.symbol == symbol and
                pos.strike == strike and
                pos.expiration == expiration and
                pos.option_type == option_type):
                old_price = pos.current_price
                pos.current_price = new_price
                pnl_pct = ((new_price - pos.average_cost) / pos.average_cost) * 100
                logger.info(
                    "[PAPER] Price updated",
                    symbol=symbol,
                    old_price=old_price,
                    new_price=new_price,
                    pnl_pct=f"{pnl_pct:.1f}%"
                )
                return

    def get_summary(self) -> dict:
        """Get paper trading summary."""
        total_pnl = 0
        for order in self.order_history:
            if order["type"] == "sell" and "pnl" in order:
                total_pnl += order["pnl"]

        return {
            "starting_cash": self.starting_cash,
            "current_cash": self.cash,
            "open_positions": len(self.positions),
            "total_orders": len(self.order_history),
            "realized_pnl": total_pnl,
        }
