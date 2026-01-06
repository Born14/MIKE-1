"""
Position Management for MIKE-1

Tracks open positions, high water marks, and exit states.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class PositionState(Enum):
    """Current state of a position."""
    PENDING = "pending"           # Order submitted, not filled
    OPEN = "open"                 # Position is active
    TRIM_1_HIT = "trim_1_hit"     # First trim executed
    TRIM_2_HIT = "trim_2_hit"     # Second trim executed
    TRAILING = "trailing"         # In trailing stop mode
    STOPPED = "stopped"           # Hit stop loss
    CLOSED = "closed"             # Fully exited
    EXPIRED = "expired"           # Closed due to DTE


class OptionType(Enum):
    """Option direction."""
    CALL = "call"
    PUT = "put"


@dataclass
class Position:
    """
    Represents an open option position.

    The Executor monitors these and enforces exit rules.
    """
    # Identity
    id: str                                    # Unique position ID
    ticker: str                                # Underlying symbol
    option_type: OptionType                    # call or put

    # Contract Details
    strike: float                              # Strike price
    expiration: str                            # Expiration date (YYYY-MM-DD)
    contracts: int                             # Number of contracts

    # Entry
    entry_price: float                         # Price paid per contract
    entry_time: datetime                       # When position was opened
    entry_cost: float = 0                      # Total cost (entry_price * contracts * 100)

    # Current State
    state: PositionState = PositionState.OPEN
    current_price: float = 0                   # Current option price
    current_value: float = 0                   # Current position value

    # High Water Mark (for trailing stop)
    high_water_mark: float = 0                 # Highest price since entry
    high_water_time: Optional[datetime] = None # When high was hit

    # ATR-based trailing stop (trails from entry, no activation threshold)
    atr_value: float = 0                       # ATR of underlying at entry
    atr_multiplier: float = 2.0               # Trail distance = ATR * multiplier
    atr_stop_active: bool = False             # Whether ATR trailing is enabled
    delta_at_entry: float = 0.35              # Option delta for ATR conversion

    # Trim Tracking
    original_contracts: int = 0                # Contracts at entry
    contracts_remaining: int = 0               # Contracts still held
    trim_1_executed: bool = False
    trim_1_price: Optional[float] = None
    trim_1_time: Optional[datetime] = None
    trim_2_executed: bool = False
    trim_2_price: Optional[float] = None
    trim_2_time: Optional[datetime] = None

    # P&L
    realized_pnl: float = 0                    # Locked in profit/loss
    unrealized_pnl: float = 0                  # Current paper P&L

    # Metadata
    grade: str = "A"                           # Trade grade (A/B)
    thesis: str = ""                           # Why you took this trade
    catalyst: str = ""                         # What triggered it
    notes: list[str] = field(default_factory=list)

    def __post_init__(self):
        """Initialize calculated fields."""
        if self.entry_cost == 0:
            self.entry_cost = self.entry_price * self.contracts * 100

        if self.original_contracts == 0:
            self.original_contracts = self.contracts

        if self.contracts_remaining == 0:
            self.contracts_remaining = self.contracts

        if self.high_water_mark == 0:
            self.high_water_mark = self.entry_price
            self.high_water_time = self.entry_time

    def update_price(self, new_price: float) -> None:
        """
        Update current price and recalculate values.

        This is called every poll cycle.
        """
        self.current_price = new_price
        self.current_value = new_price * self.contracts_remaining * 100

        # Update high water mark
        if new_price > self.high_water_mark:
            self.high_water_mark = new_price
            self.high_water_time = datetime.now()

        # Calculate unrealized P&L
        cost_basis = self.entry_price * self.contracts_remaining * 100
        self.unrealized_pnl = self.current_value - cost_basis

    @property
    def pnl_percent(self) -> float:
        """Current P&L as percentage of entry."""
        if self.entry_price == 0:
            return 0
        return ((self.current_price - self.entry_price) / self.entry_price) * 100

    @property
    def high_water_pnl_percent(self) -> float:
        """High water mark P&L as percentage."""
        if self.entry_price == 0:
            return 0
        return ((self.high_water_mark - self.entry_price) / self.entry_price) * 100

    @property
    def drawdown_from_high(self) -> float:
        """Current drawdown from high water mark as percentage."""
        if self.high_water_mark == 0:
            return 0
        return ((self.high_water_mark - self.current_price) / self.high_water_mark) * 100

    @property
    def days_to_expiration(self) -> int:
        """Calculate DTE (using dates only, not time)."""
        exp_date = datetime.strptime(self.expiration, "%Y-%m-%d").date()
        today = datetime.now().date()
        return (exp_date - today).days

    def should_trim_1(self, trigger_pct: float) -> bool:
        """Check if first trim should execute."""
        return (
            not self.trim_1_executed
            and self.pnl_percent >= trigger_pct
            and self.state == PositionState.OPEN
        )

    def should_trim_2(self, trigger_pct: float) -> bool:
        """Check if second trim should execute."""
        return (
            self.trim_1_executed
            and not self.trim_2_executed
            and self.pnl_percent >= trigger_pct
            and self.state == PositionState.TRIM_1_HIT
        )

    def should_trailing_stop(self, stop_pct: float) -> bool:
        """Check if trailing stop should trigger (percentage-based)."""
        return (
            self.trim_1_executed  # Only trail after first trim
            and self.drawdown_from_high >= stop_pct
        )

    def should_atr_trailing_stop(self) -> bool:
        """
        Check if ATR-based trailing stop should trigger.

        Simple approach: multiplier * 10 = trailing stop percentage
        - multiplier 2.0 = 20% trailing stop
        - multiplier 2.5 = 25% trailing stop

        Trails from entry - no activation threshold needed.
        """
        if not self.atr_stop_active:
            return False

        # Simple: multiplier * 10 = stop percentage
        stop_pct = self.atr_multiplier * 10

        return self.drawdown_from_high >= stop_pct

    @property
    def atr_stop_level(self) -> float:
        """Current ATR trailing stop price level."""
        if not self.atr_stop_active:
            return 0

        stop_pct = self.atr_multiplier * 10
        return self.high_water_mark * (1 - stop_pct / 100)

    @property
    def atr_stop_distance_pct(self) -> float:
        """ATR stop distance as percentage."""
        if not self.atr_stop_active:
            return 0
        return self.atr_multiplier * 10

    def should_hard_stop(self, stop_pct: float) -> bool:
        """Check if hard stop should trigger."""
        return self.pnl_percent <= -stop_pct

    def should_force_close(self, min_dte: int) -> bool:
        """Check if position should be closed due to expiration."""
        return self.days_to_expiration <= min_dte

    def should_0dte_force_close(self, force_close_time: str) -> bool:
        """
        Check if 0DTE position should be force closed based on time.

        Args:
            force_close_time: Time to force close in "HH:MM" format (ET)

        Returns:
            True if position is 0DTE and current time >= force_close_time
        """
        if self.days_to_expiration != 0:
            return False

        # Parse force close time
        hour, minute = map(int, force_close_time.split(":"))
        now = datetime.now()

        # Compare current time to force close time
        # Note: Assumes system is running in ET or adjust accordingly
        force_close_dt = now.replace(hour=hour, minute=minute, second=0, microsecond=0)

        return now >= force_close_dt

    def record_trim(self, trim_number: int, price: float, contracts_sold: int) -> None:
        """Record a trim execution."""
        now = datetime.now()
        pnl = (price - self.entry_price) * contracts_sold * 100

        if trim_number == 1:
            self.trim_1_executed = True
            self.trim_1_price = price
            self.trim_1_time = now
            self.state = PositionState.TRIM_1_HIT
        elif trim_number == 2:
            self.trim_2_executed = True
            self.trim_2_price = price
            self.trim_2_time = now
            self.state = PositionState.TRIM_2_HIT

        self.contracts_remaining -= contracts_sold
        self.realized_pnl += pnl

    def close(self, price: float, reason: str) -> None:
        """Close the position entirely."""
        pnl = (price - self.entry_price) * self.contracts_remaining * 100
        self.realized_pnl += pnl
        self.contracts_remaining = 0
        self.current_price = price

        if reason == "stop":
            self.state = PositionState.STOPPED
        elif reason == "expired":
            self.state = PositionState.EXPIRED
        else:
            self.state = PositionState.CLOSED

        self.notes.append(f"Closed: {reason} at ${price:.2f}")

    def to_dict(self) -> dict:
        """Convert to dictionary for logging/storage."""
        result = {
            "id": self.id,
            "ticker": self.ticker,
            "option_type": self.option_type.value,
            "strike": self.strike,
            "expiration": self.expiration,
            "contracts": self.contracts,
            "entry_price": self.entry_price,
            "entry_time": self.entry_time.isoformat(),
            "entry_cost": self.entry_cost,
            "state": self.state.value,
            "current_price": self.current_price,
            "high_water_mark": self.high_water_mark,
            "pnl_percent": self.pnl_percent,
            "realized_pnl": self.realized_pnl,
            "unrealized_pnl": self.unrealized_pnl,
            "grade": self.grade,
            "thesis": self.thesis,
            "catalyst": self.catalyst,
        }
        # Add ATR info if active
        if self.atr_stop_active:
            result["atr_value"] = self.atr_value
            result["atr_multiplier"] = self.atr_multiplier
            result["atr_stop_level"] = self.atr_stop_level
        return result
