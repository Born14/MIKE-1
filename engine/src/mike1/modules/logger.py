"""
Trade Logger for MIKE-1

Records everything. No editing. No deletion.
The truth, always.
"""

from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Optional, Any
import json
import os
from pathlib import Path
import structlog

from ..core.position import Position
from ..core.trade import Trade, TradeSignal


logger = structlog.get_logger()


@dataclass
class TradeLog:
    """A complete trade record."""
    # Identity
    id: str
    position_id: str

    # Signal
    ticker: str
    direction: str
    catalyst_type: str
    catalyst_description: str
    catalyst_time: str

    # Grading
    grade: str
    score: int
    score_breakdown: dict

    # Entry
    entry_time: str
    entry_price: float
    contracts: int
    strike: float
    expiration: str
    entry_cost: float

    # Exit
    exit_time: Optional[str] = None
    exit_price: Optional[float] = None
    exit_reason: Optional[str] = None
    exit_proceeds: Optional[float] = None

    # P&L
    realized_pnl: Optional[float] = None
    pnl_percent: Optional[float] = None
    high_water_mark: Optional[float] = None
    high_water_pnl_percent: Optional[float] = None

    # Trims
    trim_1_time: Optional[str] = None
    trim_1_price: Optional[float] = None
    trim_1_pnl: Optional[float] = None
    trim_2_time: Optional[str] = None
    trim_2_price: Optional[float] = None
    trim_2_pnl: Optional[float] = None

    # Meta
    config_version: str = ""
    environment: str = ""
    notes: list = None

    def __post_init__(self):
        if self.notes is None:
            self.notes = []


@dataclass
class ActionLog:
    """Log of an executor action."""
    timestamp: str
    action_type: str
    position_id: str
    ticker: str
    details: dict
    dry_run: bool


class TradeLogger:
    """
    The Logger.

    Records:
    - Every signal detected
    - Every grade assigned
    - Every entry
    - Every exit
    - Every trim
    - Every stop
    - P&L

    Data is append-only. No editing. No deletion.
    """

    def __init__(self, log_dir: str = "logs", db_url: Optional[str] = None):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)

        self.db_url = db_url or os.environ.get("DATABASE_URL")

        # Current day's log file
        self._current_date = None
        self._trades_file = None
        self._actions_file = None

        self._ensure_files()

    def _ensure_files(self) -> None:
        """Ensure log files exist for today."""
        today = datetime.now().strftime("%Y-%m-%d")

        if self._current_date != today:
            self._current_date = today
            self._trades_file = self.log_dir / f"trades_{today}.jsonl"
            self._actions_file = self.log_dir / f"actions_{today}.jsonl"

    def _append_jsonl(self, file_path: Path, data: dict) -> None:
        """Append a JSON line to file."""
        with open(file_path, "a") as f:
            f.write(json.dumps(data) + "\n")

    # =========================================================================
    # SIGNAL LOGGING
    # =========================================================================

    def log_signal(self, signal: TradeSignal) -> None:
        """Log a detected signal."""
        self._ensure_files()

        log_entry = {
            "type": "signal",
            "timestamp": datetime.now().isoformat(),
            "data": signal.to_dict()
        }

        self._append_jsonl(self._actions_file, log_entry)

        logger.info(
            "Signal logged",
            ticker=signal.ticker,
            direction=signal.direction,
            catalyst=signal.catalyst_type
        )

    # =========================================================================
    # TRADE LOGGING
    # =========================================================================

    def log_trade_entry(
        self,
        trade: Trade,
        position: Position,
        config_version: str = "",
        environment: str = ""
    ) -> None:
        """Log a trade entry."""
        self._ensure_files()

        trade_log = TradeLog(
            id=trade.signal.id,
            position_id=position.id,
            ticker=trade.ticker,
            direction=trade.direction,
            catalyst_type=trade.signal.catalyst_type,
            catalyst_description=trade.signal.catalyst_description,
            catalyst_time=trade.signal.catalyst_time.isoformat(),
            grade=trade.grade.value,
            score=trade.signal.score.points if trade.signal.score else 0,
            score_breakdown=trade.signal.score.breakdown if trade.signal.score else {},
            entry_time=position.entry_time.isoformat(),
            entry_price=position.entry_price,
            contracts=position.contracts,
            strike=position.strike,
            expiration=position.expiration,
            entry_cost=position.entry_cost,
            config_version=config_version,
            environment=environment,
        )

        self._append_jsonl(self._trades_file, asdict(trade_log))

        logger.info(
            "Trade entry logged",
            ticker=trade.ticker,
            grade=trade.grade.value,
            entry_price=position.entry_price
        )

    def log_trade_exit(
        self,
        position: Position,
        reason: str
    ) -> None:
        """Log a trade exit (update existing record)."""
        self._ensure_files()

        exit_data = {
            "type": "exit",
            "timestamp": datetime.now().isoformat(),
            "position_id": position.id,
            "ticker": position.ticker,
            "exit_price": position.current_price,
            "exit_reason": reason,
            "realized_pnl": position.realized_pnl,
            "pnl_percent": position.pnl_percent,
            "high_water_mark": position.high_water_mark,
            "high_water_pnl_percent": position.high_water_pnl_percent,
        }

        self._append_jsonl(self._actions_file, exit_data)

        logger.info(
            "Trade exit logged",
            ticker=position.ticker,
            reason=reason,
            pnl=f"${position.realized_pnl:.2f}",
            pnl_pct=f"{position.pnl_percent:.1f}%"
        )

    def log_trim(
        self,
        position: Position,
        trim_number: int,
        price: float,
        contracts_sold: int,
        pnl: float
    ) -> None:
        """Log a trim execution."""
        self._ensure_files()

        trim_data = {
            "type": f"trim_{trim_number}",
            "timestamp": datetime.now().isoformat(),
            "position_id": position.id,
            "ticker": position.ticker,
            "trim_number": trim_number,
            "price": price,
            "contracts_sold": contracts_sold,
            "pnl": pnl,
            "pnl_percent": ((price - position.entry_price) / position.entry_price) * 100,
        }

        self._append_jsonl(self._actions_file, trim_data)

        logger.info(
            f"Trim {trim_number} logged",
            ticker=position.ticker,
            price=price,
            pnl=f"${pnl:.2f}"
        )

    # =========================================================================
    # ACTION LOGGING
    # =========================================================================

    def log_action(
        self,
        action_type: str,
        position_id: str,
        ticker: str,
        details: dict,
        dry_run: bool = False
    ) -> None:
        """Log an executor action."""
        self._ensure_files()

        action_log = ActionLog(
            timestamp=datetime.now().isoformat(),
            action_type=action_type,
            position_id=position_id,
            ticker=ticker,
            details=details,
            dry_run=dry_run
        )

        self._append_jsonl(self._actions_file, asdict(action_log))

    # =========================================================================
    # SYSTEM LOGGING
    # =========================================================================

    def log_system_event(self, event: str, details: dict = None) -> None:
        """Log a system event."""
        self._ensure_files()

        event_data = {
            "type": "system",
            "timestamp": datetime.now().isoformat(),
            "event": event,
            "details": details or {}
        }

        self._append_jsonl(self._actions_file, event_data)

    def log_governor_event(self, event: str, details: dict = None) -> None:
        """Log a risk governor event."""
        self._ensure_files()

        event_data = {
            "type": "governor",
            "timestamp": datetime.now().isoformat(),
            "event": event,
            "details": details or {}
        }

        self._append_jsonl(self._actions_file, event_data)

    # =========================================================================
    # READ LOGS
    # =========================================================================

    def get_trades(self, date: Optional[str] = None) -> list[dict]:
        """Get trades for a date (default today)."""
        if date is None:
            date = datetime.now().strftime("%Y-%m-%d")

        trades_file = self.log_dir / f"trades_{date}.jsonl"

        if not trades_file.exists():
            return []

        trades = []
        with open(trades_file, "r") as f:
            for line in f:
                if line.strip():
                    trades.append(json.loads(line))

        return trades

    def get_actions(self, date: Optional[str] = None) -> list[dict]:
        """Get actions for a date (default today)."""
        if date is None:
            date = datetime.now().strftime("%Y-%m-%d")

        actions_file = self.log_dir / f"actions_{date}.jsonl"

        if not actions_file.exists():
            return []

        actions = []
        with open(actions_file, "r") as f:
            for line in f:
                if line.strip():
                    actions.append(json.loads(line))

        return actions

    def get_daily_summary(self, date: Optional[str] = None) -> dict:
        """Get summary statistics for a day."""
        trades = self.get_trades(date)
        actions = self.get_actions(date)

        if not trades:
            return {
                "date": date or datetime.now().strftime("%Y-%m-%d"),
                "trades": 0,
                "wins": 0,
                "losses": 0,
                "win_rate": 0,
                "total_pnl": 0,
                "avg_win": 0,
                "avg_loss": 0,
            }

        # Calculate stats
        exits = [a for a in actions if a.get("type") == "exit"]

        wins = [e for e in exits if e.get("realized_pnl", 0) > 0]
        losses = [e for e in exits if e.get("realized_pnl", 0) < 0]

        total_pnl = sum(e.get("realized_pnl", 0) for e in exits)
        avg_win = sum(e.get("realized_pnl", 0) for e in wins) / len(wins) if wins else 0
        avg_loss = sum(e.get("realized_pnl", 0) for e in losses) / len(losses) if losses else 0

        return {
            "date": date or datetime.now().strftime("%Y-%m-%d"),
            "trades": len(trades),
            "exits": len(exits),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": len(wins) / len(exits) * 100 if exits else 0,
            "total_pnl": total_pnl,
            "avg_win": avg_win,
            "avg_loss": avg_loss,
        }
