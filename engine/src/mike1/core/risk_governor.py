"""
Risk Governor for MIKE-1

The absolute authority. No trade bypasses this.
"""

from dataclasses import dataclass, field
from datetime import datetime, date
from typing import Optional
import structlog

from .config import Config, get_config
from .trade import Trade, TradeGrade

logger = structlog.get_logger()


@dataclass
class DailyState:
    """Tracks daily trading state."""
    date: date = field(default_factory=date.today)
    trades_executed: int = 0
    realized_pnl: float = 0
    unrealized_pnl: float = 0
    positions_opened: int = 0
    positions_closed: int = 0
    locked_out: bool = False
    lockout_reason: Optional[str] = None
    lockout_time: Optional[datetime] = None

    def reset(self) -> None:
        """Reset for new day."""
        self.date = date.today()
        self.trades_executed = 0
        self.realized_pnl = 0
        self.unrealized_pnl = 0
        self.positions_opened = 0
        self.positions_closed = 0
        self.locked_out = False
        self.lockout_reason = None
        self.lockout_time = None


class RiskGovernor:
    """
    The Risk Governor.

    This module has absolute authority over all trading activity.
    It enforces:
    - Position sizing limits
    - Daily trade limits
    - Daily loss limits
    - Kill switch

    No module can override the Governor.
    """

    def __init__(self, config: Optional[Config] = None):
        self.config = config or get_config()
        self.daily_state = DailyState()
        self._check_new_day()

    def _check_new_day(self) -> None:
        """Reset state if it's a new day."""
        if self.daily_state.date != date.today():
            logger.info(
                "New trading day",
                previous_date=self.daily_state.date.isoformat(),
                new_date=date.today().isoformat(),
            )
            self.daily_state.reset()

    def _lockout(self, reason: str) -> None:
        """Lock out trading for the day."""
        self.daily_state.locked_out = True
        self.daily_state.lockout_reason = reason
        self.daily_state.lockout_time = datetime.now()
        logger.warning("LOCKOUT ACTIVATED", reason=reason)

    # =========================================================================
    # PERMISSION CHECKS
    # =========================================================================

    def can_trade(self) -> tuple[bool, str]:
        """
        Master check: Can we trade right now?

        Returns:
            (allowed, reason)
        """
        self._check_new_day()

        # Check kill switch
        if self.config.risk.kill_switch:
            return False, "Kill switch is active"

        # Check if system is armed
        if not self.config.armed:
            return False, "System is not armed"

        # Check if locked out
        if self.daily_state.locked_out:
            return False, f"Locked out: {self.daily_state.lockout_reason}"

        # Check daily trade limit
        if self.daily_state.trades_executed >= self.config.risk.max_trades_per_day:
            return False, f"Daily trade limit reached ({self.config.risk.max_trades_per_day})"

        # Check daily loss limit
        if self.daily_state.realized_pnl <= -self.config.risk.max_daily_loss:
            self._lockout(f"Daily loss limit hit (${self.daily_state.realized_pnl:.2f})")
            return False, "Daily loss limit exceeded"

        return True, "OK"

    def validate_trade(self, trade: Trade) -> tuple[bool, str]:
        """
        Validate a specific trade before execution.

        Returns:
            (allowed, reason)
        """
        # First check general trading permissions
        can_trade, reason = self.can_trade()
        if not can_trade:
            return False, reason

        # Check trade grade
        if trade.grade == TradeGrade.NO_TRADE:
            return False, "Trade grade is NO_TRADE"

        # Check position size
        if trade.contracts > self.config.risk.max_contracts:
            return False, f"Contracts ({trade.contracts}) exceeds max ({self.config.risk.max_contracts})"

        # Check risk amount
        if trade.max_risk > self.config.risk.max_risk_per_trade:
            return False, f"Risk (${trade.max_risk}) exceeds max (${self.config.risk.max_risk_per_trade})"

        return True, "OK"

    def validate_exit(self, reason: str) -> tuple[bool, str]:
        """
        Validate an exit order.

        Exits are almost always allowed - we want to get OUT of positions.
        """
        # Exits are allowed even during lockout
        # The only thing that stops an exit is kill switch
        if self.config.risk.kill_switch:
            return False, "Kill switch is active - manual intervention required"

        return True, "OK"

    # =========================================================================
    # STATE UPDATES
    # =========================================================================

    def record_trade(self, trade: Trade) -> None:
        """Record that a trade was executed."""
        self._check_new_day()
        self.daily_state.trades_executed += 1
        self.daily_state.positions_opened += 1

        logger.info(
            "Trade recorded",
            ticker=trade.ticker,
            grade=trade.grade.value,
            trades_today=self.daily_state.trades_executed,
            max_trades=self.config.risk.max_trades_per_day,
        )

    def record_pnl(self, realized: float, unrealized: float = 0) -> None:
        """Update P&L tracking."""
        self._check_new_day()
        self.daily_state.realized_pnl += realized
        self.daily_state.unrealized_pnl = unrealized  # Replace, don't add

        logger.info(
            "P&L updated",
            realized_today=self.daily_state.realized_pnl,
            unrealized=unrealized,
        )

        # Check if we've hit daily loss limit
        if self.daily_state.realized_pnl <= -self.config.risk.max_daily_loss:
            self._lockout(f"Daily loss limit hit: ${self.daily_state.realized_pnl:.2f}")

    def record_close(self) -> None:
        """Record that a position was closed."""
        self.daily_state.positions_closed += 1

    # =========================================================================
    # SIZING
    # =========================================================================

    def calculate_size(self, trade: Trade, contract_price: float) -> int:
        """
        Calculate position size based on grade and risk limits.

        Args:
            trade: The trade to size
            contract_price: Price per contract (premium)

        Returns:
            Number of contracts to buy
        """
        max_risk = self.config.risk.max_risk_per_trade
        max_contracts = self.config.risk.max_contracts

        # Cost per contract (premium * 100)
        cost_per_contract = contract_price * 100

        # Calculate max contracts we can afford
        affordable = int(max_risk / cost_per_contract) if cost_per_contract > 0 else 0

        # Apply grade-based sizing
        if trade.grade == TradeGrade.A:
            # A-tier: up to max
            contracts = min(affordable, max_contracts)
        elif trade.grade == TradeGrade.B:
            # B-tier: minimum exposure (1 contract or 0 if too expensive)
            contracts = 1 if affordable >= 1 else 0
        else:
            # No trade
            contracts = 0

        return contracts

    # =========================================================================
    # EMERGENCY CONTROLS
    # =========================================================================

    def activate_kill_switch(self, reason: str = "Manual activation") -> None:
        """
        Activate the kill switch.

        This stops ALL trading activity immediately.
        """
        self.config.risk.kill_switch = True
        self._lockout(f"KILL SWITCH: {reason}")
        logger.critical("KILL SWITCH ACTIVATED", reason=reason)

    def deactivate_kill_switch(self) -> None:
        """
        Deactivate the kill switch.

        Should only be done manually, deliberately.
        """
        self.config.risk.kill_switch = False
        logger.warning("Kill switch deactivated - trading may resume")

    def force_lockout(self, reason: str) -> None:
        """Force a lockout for the rest of the day."""
        self._lockout(reason)

    # =========================================================================
    # STATUS
    # =========================================================================

    def get_status(self) -> dict:
        """Get current governor status."""
        self._check_new_day()

        can_trade, reason = self.can_trade()

        return {
            "can_trade": can_trade,
            "reason": reason,
            "armed": self.config.armed,
            "kill_switch": self.config.risk.kill_switch,
            "environment": self.config.environment,
            "daily": {
                "date": self.daily_state.date.isoformat(),
                "trades_executed": self.daily_state.trades_executed,
                "trades_remaining": max(0, self.config.risk.max_trades_per_day - self.daily_state.trades_executed),
                "realized_pnl": self.daily_state.realized_pnl,
                "loss_limit_remaining": self.config.risk.max_daily_loss + self.daily_state.realized_pnl,
                "locked_out": self.daily_state.locked_out,
                "lockout_reason": self.daily_state.lockout_reason,
            },
            "limits": {
                "max_risk_per_trade": self.config.risk.max_risk_per_trade,
                "max_contracts": self.config.risk.max_contracts,
                "max_trades_per_day": self.config.risk.max_trades_per_day,
                "max_daily_loss": self.config.risk.max_daily_loss,
            },
        }

    def __str__(self) -> str:
        status = self.get_status()
        return (
            f"RiskGovernor: {'ARMED' if status['armed'] else 'DISARMED'} | "
            f"Trades: {status['daily']['trades_executed']}/{status['limits']['max_trades_per_day']} | "
            f"P&L: ${status['daily']['realized_pnl']:.2f}"
        )
