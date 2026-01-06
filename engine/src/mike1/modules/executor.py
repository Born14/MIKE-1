"""
Executor Module for MIKE-1

The core execution engine. Monitors positions and enforces exits.
No emotion. No negotiation.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
import structlog

from ..core.config import Config, get_config
from ..core.position import Position, PositionState, OptionType
from ..core.risk_governor import RiskGovernor
from ..core.trade import Trade, TradeGrade
from .broker import Broker, OptionPosition


logger = structlog.get_logger()


@dataclass
class ExecutorState:
    """Current state of the executor."""
    running: bool = False
    last_poll: Optional[datetime] = None
    positions: dict[str, Position] = field(default_factory=dict)
    pending_orders: list[str] = field(default_factory=list)


class Executor:
    """
    The Executor.

    Responsibilities:
    - Monitor open positions
    - Track high water marks
    - Execute trims at targets
    - Execute stops (trailing and hard)
    - Handle forced exits (DTE)

    The Executor does NOT:
    - Decide what to trade
    - Score opportunities
    - Override risk limits

    It simply executes the rules. Every time. Without exception.
    """

    def __init__(
        self,
        broker: Broker,
        config: Optional[Config] = None,
        risk_governor: Optional[RiskGovernor] = None,
        dry_run: bool = True
    ):
        self.broker = broker
        self.config = config or get_config()
        self.governor = risk_governor or RiskGovernor(self.config)
        self.dry_run = dry_run
        self.state = ExecutorState()

        logger.info(
            "Executor initialized",
            dry_run=self.dry_run,
            armed=self.config.armed
        )

    # =========================================================================
    # POSITION SYNC
    # =========================================================================

    def sync_positions(self) -> None:
        """
        Sync positions from broker.

        This pulls current positions and updates our tracking.
        """
        broker_positions = self.broker.get_option_positions()

        for bp in broker_positions:
            pos_id = bp.id

            if pos_id in self.state.positions:
                # Update existing position
                self._update_position(pos_id, bp)
            else:
                # New position (opened externally or missed)
                self._track_new_position(bp)

        # Check for positions that no longer exist
        broker_ids = {bp.id for bp in broker_positions}
        for pos_id in list(self.state.positions.keys()):
            if pos_id not in broker_ids:
                pos = self.state.positions[pos_id]
                if pos.state not in [PositionState.CLOSED, PositionState.STOPPED]:
                    pos.state = PositionState.CLOSED
                    logger.info("Position closed externally", position_id=pos_id)

    def _update_position(self, pos_id: str, broker_pos: OptionPosition) -> None:
        """Update an existing position with current data."""
        pos = self.state.positions[pos_id]
        pos.update_price(broker_pos.current_price)
        pos.contracts_remaining = int(broker_pos.quantity)

    def _track_new_position(self, broker_pos: OptionPosition) -> None:
        """Start tracking a new position."""
        pos = Position(
            id=broker_pos.id,
            ticker=broker_pos.symbol,
            option_type=OptionType.CALL if broker_pos.option_type == "call" else OptionType.PUT,
            strike=broker_pos.strike,
            expiration=broker_pos.expiration,
            contracts=int(broker_pos.quantity),
            entry_price=broker_pos.average_cost,
            entry_time=broker_pos.created_at or datetime.now(),
        )
        pos.update_price(broker_pos.current_price)

        # Set up ATR-based trailing for single contract positions
        atr_config = self.config.exits.atr_trailing
        if int(broker_pos.quantity) == 1 and atr_config.enabled:
            atr = self.broker.get_atr(broker_pos.symbol, atr_config.period)
            if atr > 0:
                pos.atr_value = atr
                pos.atr_multiplier = atr_config.multiplier
                pos.atr_stop_active = True
                # Estimate delta from option price vs underlying
                # Default to 0.35 if we can't calculate
                pos.delta_at_entry = 0.35
                logger.info(
                    "ATR trailing enabled from entry",
                    ticker=pos.ticker,
                    atr=f"${atr:.2f}",
                    multiplier=atr_config.multiplier,
                    stop_distance=f"${atr * atr_config.multiplier * pos.delta_at_entry:.3f}"
                )

        self.state.positions[broker_pos.id] = pos

        logger.info(
            "Now tracking position",
            ticker=pos.ticker,
            strike=pos.strike,
            contracts=pos.contracts,
            entry_price=pos.entry_price
        )

    # =========================================================================
    # EXIT LOGIC
    # =========================================================================

    def check_exits(self) -> list[dict]:
        """
        Check all positions for exit conditions.

        Returns list of actions taken.
        """
        actions = []

        for pos_id, pos in self.state.positions.items():
            if pos.state in [PositionState.CLOSED, PositionState.STOPPED, PositionState.EXPIRED]:
                continue

            if pos.contracts_remaining <= 0:
                continue

            action = self._evaluate_position(pos)
            if action:
                actions.append(action)

        return actions

    def _evaluate_position(self, pos: Position) -> Optional[dict]:
        """
        Evaluate a single position for exit conditions.

        Priority order:
        1. Hard stop (non-negotiable)
        2. 0DTE time-based force close (3:30 PM ET)
        3. DTE force close
        4. ATR trailing stop (single contracts, trails from entry)
        5. Percentage trailing stop (multi-contracts, after trim 1)
        6. Trim targets
        """
        exits = self.config.exits

        # 1. HARD STOP - Check first, always
        if pos.should_hard_stop(exits.hard_stop_pct):
            return self._execute_hard_stop(pos)

        # 2. 0DTE TIME-BASED FORCE CLOSE (before Alpaca cutoff)
        if pos.should_0dte_force_close(exits.force_close_0dte_time):
            return self._execute_0dte_close(pos)

        # 3. DTE FORCE CLOSE
        if pos.should_force_close(exits.close_at_dte):
            return self._execute_dte_close(pos)

        # 4. ATR TRAILING STOP (single contracts - trails from entry)
        if pos.should_atr_trailing_stop():
            return self._execute_atr_trailing_stop(pos)

        # 5. PERCENTAGE TRAILING STOP (multi-contracts, after trim 1)
        if pos.should_trailing_stop(exits.trailing_stop_pct):
            return self._execute_trailing_stop(pos)

        # 6. TRIM 2
        if pos.should_trim_2(exits.trim_2.trigger_pct):
            return self._execute_trim(pos, 2)

        # 7. TRIM 1
        if pos.should_trim_1(exits.trim_1.trigger_pct):
            return self._execute_trim(pos, 1)

        return None

    def _execute_hard_stop(self, pos: Position) -> dict:
        """Execute hard stop loss."""
        logger.warning(
            "HARD STOP TRIGGERED",
            ticker=pos.ticker,
            pnl_pct=f"{pos.pnl_percent:.1f}%",
            current_price=pos.current_price,
            entry_price=pos.entry_price
        )

        action = {
            "type": "hard_stop",
            "position_id": str(pos.id),
            "ticker": pos.ticker,
            "contracts": pos.contracts_remaining,
            "price": pos.current_price,
            "pnl_pct": pos.pnl_percent,
            "executed": False
        }

        if not self.dry_run:
            result = self._sell_position(pos, pos.contracts_remaining, "hard_stop")
            action["executed"] = result.success
            action["order_id"] = result.order_id

            if result.success:
                pos.close(pos.current_price, "stop")
                self.governor.record_pnl(pos.realized_pnl)
                self.governor.record_close()
        else:
            logger.info("[DRY RUN] Would execute hard stop", **action)

        return action

    def _execute_0dte_close(self, pos: Position) -> dict:
        """Force close 0DTE position before Alpaca cutoff."""
        logger.warning(
            "0DTE FORCE CLOSE (time-based)",
            ticker=pos.ticker,
            expiration=pos.expiration,
            pnl_pct=f"{pos.pnl_percent:.1f}%",
            force_close_time=self.config.exits.force_close_0dte_time
        )

        action = {
            "type": "0dte_close",
            "position_id": str(pos.id),
            "ticker": pos.ticker,
            "contracts": pos.contracts_remaining,
            "price": pos.current_price,
            "pnl_pct": pos.pnl_percent,
            "reason": "0DTE time-based force close",
            "executed": False
        }

        if not self.dry_run:
            result = self._sell_position(pos, pos.contracts_remaining, "0dte_close")
            action["executed"] = result.success
            action["order_id"] = result.order_id

            if result.success:
                pos.close(pos.current_price, "expired")
                self.governor.record_pnl(pos.realized_pnl)
                self.governor.record_close()
        else:
            logger.info("[DRY RUN] Would execute 0DTE close", **action)

        return action

    def _execute_dte_close(self, pos: Position) -> dict:
        """Force close due to expiration."""
        logger.warning(
            "DTE FORCE CLOSE",
            ticker=pos.ticker,
            dte=pos.days_to_expiration,
            pnl_pct=f"{pos.pnl_percent:.1f}%"
        )

        action = {
            "type": "dte_close",
            "position_id": str(pos.id),
            "ticker": pos.ticker,
            "contracts": pos.contracts_remaining,
            "price": pos.current_price,
            "dte": pos.days_to_expiration,
            "executed": False
        }

        if not self.dry_run:
            result = self._sell_position(pos, pos.contracts_remaining, "dte_close")
            action["executed"] = result.success
            action["order_id"] = result.order_id

            if result.success:
                pos.close(pos.current_price, "expired")
                self.governor.record_pnl(pos.realized_pnl)
                self.governor.record_close()
        else:
            logger.info("[DRY RUN] Would execute DTE close", **action)

        return action

    def _execute_atr_trailing_stop(self, pos: Position) -> dict:
        """Execute ATR-based trailing stop (trails from entry)."""
        logger.info(
            "ATR TRAILING STOP TRIGGERED",
            ticker=pos.ticker,
            high_water=f"${pos.high_water_mark:.2f}",
            current=f"${pos.current_price:.2f}",
            stop_level=f"${pos.atr_stop_level:.2f}",
            atr=f"${pos.atr_value:.2f}",
            pnl=f"{pos.pnl_percent:.1f}%"
        )

        action = {
            "type": "atr_trailing_stop",
            "position_id": str(pos.id),
            "ticker": pos.ticker,
            "contracts": pos.contracts_remaining,
            "price": pos.current_price,
            "high_water": pos.high_water_mark,
            "stop_level": pos.atr_stop_level,
            "atr": pos.atr_value,
            "pnl_pct": pos.pnl_percent,
            "executed": False
        }

        if not self.dry_run:
            result = self._sell_position(pos, pos.contracts_remaining, "atr_trailing_stop")
            action["executed"] = result.success
            action["order_id"] = result.order_id

            if result.success:
                pos.close(pos.current_price, "trailing_stop")
                self.governor.record_pnl(pos.realized_pnl)
                self.governor.record_close()
        else:
            logger.info("[DRY RUN] Would execute ATR trailing stop", **action)

        return action

    def _execute_trailing_stop(self, pos: Position) -> dict:
        """Execute percentage-based trailing stop."""
        logger.info(
            "TRAILING STOP TRIGGERED",
            ticker=pos.ticker,
            high_water=pos.high_water_mark,
            current=pos.current_price,
            drawdown=f"{pos.drawdown_from_high:.1f}%",
            locked_pnl=f"{pos.high_water_pnl_percent:.1f}%"
        )

        action = {
            "type": "trailing_stop",
            "position_id": str(pos.id),
            "ticker": pos.ticker,
            "contracts": pos.contracts_remaining,
            "price": pos.current_price,
            "high_water": pos.high_water_mark,
            "drawdown_pct": pos.drawdown_from_high,
            "executed": False
        }

        if not self.dry_run:
            result = self._sell_position(pos, pos.contracts_remaining, "trailing_stop")
            action["executed"] = result.success
            action["order_id"] = result.order_id

            if result.success:
                pos.close(pos.current_price, "trailing_stop")
                self.governor.record_pnl(pos.realized_pnl)
                self.governor.record_close()
        else:
            logger.info("[DRY RUN] Would execute trailing stop", **action)

        return action

    def _execute_trim(self, pos: Position, trim_number: int) -> Optional[dict]:
        """Execute a trim."""
        # For single contract positions, skip actual trim but activate trailing stop
        if pos.contracts_remaining == 1 and trim_number == 1:
            if not pos.trim_1_executed:
                pos.trim_1_executed = True  # Activate trailing stop
                pos.state = PositionState.TRIM_1_HIT
                logger.info(
                    "Single contract hit +25% - trailing stop now active",
                    ticker=pos.ticker,
                    pnl_pct=f"{pos.pnl_percent:.1f}%",
                    high_water=f"{pos.high_water_pnl_percent:.1f}%"
                )
            return None

        # For single contract positions at +50%, skip trim 2 - let trailing stop manage exit
        if pos.contracts_remaining == 1 and trim_number == 2:
            logger.info(
                "Single contract at +50% - continuing to trail (no upside cap)",
                ticker=pos.ticker,
                pnl_pct=f"{pos.pnl_percent:.1f}%",
                high_water=f"{pos.high_water_pnl_percent:.1f}%"
            )
            return None

        if trim_number == 1:
            trim_config = self.config.exits.trim_1
        else:
            trim_config = self.config.exits.trim_2

        contracts_to_sell = int(pos.contracts_remaining * (trim_config.sell_pct / 100))
        contracts_to_sell = max(1, contracts_to_sell)  # At least 1

        if contracts_to_sell > pos.contracts_remaining:
            contracts_to_sell = pos.contracts_remaining

        logger.info(
            f"TRIM {trim_number} TRIGGERED",
            ticker=pos.ticker,
            pnl_pct=f"{pos.pnl_percent:.1f}%",
            selling=contracts_to_sell,
            remaining=pos.contracts_remaining - contracts_to_sell
        )

        action = {
            "type": f"trim_{trim_number}",
            "position_id": str(pos.id),
            "ticker": pos.ticker,
            "contracts": contracts_to_sell,
            "price": pos.current_price,
            "pnl_pct": pos.pnl_percent,
            "executed": False
        }

        if not self.dry_run:
            result = self._sell_position(pos, contracts_to_sell, f"trim_{trim_number}")
            action["executed"] = result.success
            action["order_id"] = result.order_id

            if result.success:
                pos.record_trim(trim_number, pos.current_price, contracts_to_sell)
                self.governor.record_pnl(
                    (pos.current_price - pos.entry_price) * contracts_to_sell * 100
                )
        else:
            logger.info(f"[DRY RUN] Would execute trim {trim_number}", **action)

        return action

    def _sell_position(self, pos: Position, quantity: int, reason: str):
        """Execute a sell order through the broker."""
        # Validate with governor
        allowed, msg = self.governor.validate_exit(reason)
        if not allowed:
            logger.error("Exit blocked by governor", reason=msg)
            return type('OrderResult', (), {'success': False, 'order_id': None})()

        result = self.broker.sell_option(
            symbol=pos.ticker,
            strike=pos.strike,
            expiration=pos.expiration,
            option_type=pos.option_type.value,
            quantity=quantity,
            price=pos.current_price  # Limit at current price
        )

        return result

    # =========================================================================
    # ENTRY EXECUTION
    # =========================================================================

    def execute_trade(self, trade: Trade) -> Optional[Position]:
        """
        Execute an approved trade.

        The Judge has already approved this. We just execute.
        """
        # Final validation with governor
        allowed, reason = self.governor.validate_trade(trade)
        if not allowed:
            logger.warning("Trade blocked by governor", reason=reason)
            trade.reject(reason)
            return None

        if trade.strike is None or trade.expiration is None:
            logger.error("Trade missing strike or expiration")
            trade.reject("Missing contract details")
            return None

        logger.info(
            "Executing trade",
            ticker=trade.ticker,
            direction=trade.direction,
            grade=trade.grade.value,
            strike=trade.strike,
            expiration=trade.expiration,
            contracts=trade.contracts
        )

        if self.dry_run:
            logger.info("[DRY RUN] Would execute trade")
            return None

        # Get current quote
        quote = self.broker.get_option_quote(
            symbol=trade.ticker,
            strike=trade.strike,
            expiration=trade.expiration,
            option_type=trade.direction
        )

        if not quote:
            trade.reject("Could not get quote")
            return None

        # Execute order
        result = self.broker.buy_option(
            symbol=trade.ticker,
            strike=trade.strike,
            expiration=trade.expiration,
            option_type=trade.direction,
            quantity=trade.contracts,
            price=quote.ask  # Pay the ask
        )

        if not result.success:
            trade.reject(result.message)
            return None

        # Create position
        pos = Position(
            id=result.order_id,
            ticker=trade.ticker,
            option_type=OptionType.CALL if trade.direction == "call" else OptionType.PUT,
            strike=trade.strike,
            expiration=trade.expiration,
            contracts=trade.contracts,
            entry_price=result.filled_price,
            entry_time=datetime.now(),
            grade=trade.grade.value,
            thesis=trade.signal.catalyst_description,
            catalyst=trade.signal.catalyst_type,
        )

        # Track it
        self.state.positions[pos.id] = pos
        trade.mark_executed(pos.id)

        # Record with governor
        self.governor.record_trade(trade)

        logger.info(
            "Trade executed",
            position_id=pos.id,
            ticker=pos.ticker,
            entry_price=pos.entry_price,
            contracts=pos.contracts
        )

        return pos

    # =========================================================================
    # MAIN LOOP
    # =========================================================================

    def poll(self) -> list[dict]:
        """
        Single poll cycle.

        Called on interval by the main engine.
        """
        self.state.last_poll = datetime.now()
        actions = []

        try:
            # Sync positions from broker
            self.sync_positions()

            # Check for exit conditions
            exit_actions = self.check_exits()
            actions.extend(exit_actions)

            # Log status
            self._log_status()

        except Exception as e:
            logger.error("Error in poll cycle", error=str(e))

        return actions

    def _log_status(self) -> None:
        """Log current executor status."""
        open_positions = [
            p for p in self.state.positions.values()
            if p.state not in [PositionState.CLOSED, PositionState.STOPPED, PositionState.EXPIRED]
        ]

        if open_positions:
            for pos in open_positions:
                log_data = {
                    "ticker": pos.ticker,
                    "state": pos.state.value,
                    "pnl": f"{pos.pnl_percent:.1f}%",
                    "high_water": f"{pos.high_water_pnl_percent:.1f}%",
                    "contracts": pos.contracts_remaining
                }
                # Add ATR stop info if active
                if pos.atr_stop_active:
                    log_data["atr_stop"] = f"${pos.atr_stop_level:.2f}"
                logger.debug("Position status", **log_data)

    # =========================================================================
    # STATUS
    # =========================================================================

    def get_status(self) -> dict:
        """Get executor status."""
        open_positions = [
            p.to_dict() for p in self.state.positions.values()
            if p.state not in [PositionState.CLOSED, PositionState.STOPPED, PositionState.EXPIRED]
        ]

        return {
            "running": self.state.running,
            "dry_run": self.dry_run,
            "last_poll": self.state.last_poll.isoformat() if self.state.last_poll else None,
            "open_positions": len(open_positions),
            "positions": open_positions,
            "governor": self.governor.get_status(),
        }
