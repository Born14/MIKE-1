"""
Trade Grading for MIKE-1

The Judge's scoring system.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class TradeGrade(Enum):
    """Trade quality grades."""
    A = "A"      # Full conviction, full size
    B = "B"      # Partial conviction, minimal size
    NO_TRADE = "NO_TRADE"  # Does not meet criteria


@dataclass
class ScoringResult:
    """Result of scoring a trade opportunity."""
    points: int
    grade: TradeGrade
    breakdown: dict[str, int] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)
    timestamp: datetime = field(default_factory=datetime.now)

    def __str__(self) -> str:
        return f"{self.grade.value} ({self.points} pts)"


@dataclass
class TradeSignal:
    """
    A potential trade opportunity detected by the Scout.

    The Judge scores these to determine if they become trades.
    """
    # Identity
    id: str
    ticker: str
    direction: str  # "call" or "put"

    # Catalyst
    catalyst_type: str         # news, earnings, technical, etc.
    catalyst_description: str  # What happened
    catalyst_time: datetime    # When it happened

    # Market State
    current_price: float
    vwap: Optional[float] = None
    volume: Optional[int] = None
    avg_volume: Optional[int] = None
    rsi: Optional[float] = None

    # Scoring
    score: Optional[ScoringResult] = None

    # Timestamps
    detected_at: datetime = field(default_factory=datetime.now)
    scored_at: Optional[datetime] = None
    executed_at: Optional[datetime] = None

    @property
    def volume_ratio(self) -> Optional[float]:
        """Volume compared to average."""
        if self.volume and self.avg_volume and self.avg_volume > 0:
            return self.volume / self.avg_volume
        return None

    @property
    def catalyst_age_hours(self) -> float:
        """Hours since catalyst occurred."""
        delta = datetime.now() - self.catalyst_time
        return delta.total_seconds() / 3600

    def to_dict(self) -> dict:
        """Convert to dictionary for logging."""
        return {
            "id": self.id,
            "ticker": self.ticker,
            "direction": self.direction,
            "catalyst_type": self.catalyst_type,
            "catalyst_description": self.catalyst_description,
            "catalyst_time": self.catalyst_time.isoformat(),
            "current_price": self.current_price,
            "vwap": self.vwap,
            "volume_ratio": self.volume_ratio,
            "score": str(self.score) if self.score else None,
            "detected_at": self.detected_at.isoformat(),
        }


@dataclass
class Trade:
    """
    A trade that has been approved by the Judge.

    This becomes a Position once executed.
    """
    # From Signal
    signal: TradeSignal

    # Trade Details
    grade: TradeGrade
    contracts: int
    max_risk: float

    # Option Selection
    strike: Optional[float] = None
    expiration: Optional[str] = None
    target_delta: Optional[float] = None

    # Status
    approved: bool = False
    approved_at: Optional[datetime] = None
    executed: bool = False
    executed_at: Optional[datetime] = None
    position_id: Optional[str] = None

    # Rejection
    rejected: bool = False
    rejection_reason: Optional[str] = None

    def approve(self) -> None:
        """Mark trade as approved for execution."""
        self.approved = True
        self.approved_at = datetime.now()

    def reject(self, reason: str) -> None:
        """Reject this trade."""
        self.rejected = True
        self.rejection_reason = reason

    def mark_executed(self, position_id: str) -> None:
        """Mark trade as executed."""
        self.executed = True
        self.executed_at = datetime.now()
        self.position_id = position_id

    @property
    def ticker(self) -> str:
        return self.signal.ticker

    @property
    def direction(self) -> str:
        return self.signal.direction

    def to_dict(self) -> dict:
        """Convert to dictionary for logging."""
        return {
            "signal": self.signal.to_dict(),
            "grade": self.grade.value,
            "contracts": self.contracts,
            "max_risk": self.max_risk,
            "strike": self.strike,
            "expiration": self.expiration,
            "approved": self.approved,
            "executed": self.executed,
            "rejected": self.rejected,
            "rejection_reason": self.rejection_reason,
        }
