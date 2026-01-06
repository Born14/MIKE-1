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

    # Priority (for Scout ranking)
    priority: int = 0  # Higher = more urgent

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


@dataclass
class OptionCandidate:
    """
    A single option contract candidate from chain scan (Curator output).
    """
    # Contract identity
    symbol: str
    strike: float
    expiration: str
    option_type: str  # "call" or "put"

    # Greeks & metrics (for filtering)
    delta: float
    dte: int
    open_interest: int
    volume: int
    bid: float
    ask: float
    spread_pct: float

    # Unusual activity detection
    vol_oi_ratio: float = 0.0
    is_unusual_activity: bool = False

    # Curator ranking score (0-100)
    # Higher = better candidate for Judge evaluation
    curator_score: float = 0.0

    # Ranking reasons (why this score?)
    ranking_reasons: list[str] = field(default_factory=list)

    @property
    def spread_dollars(self) -> float:
        """Bid-ask spread in dollars."""
        return self.ask - self.bid

    def to_dict(self) -> dict:
        """Convert to dictionary for logging."""
        return {
            "symbol": self.symbol,
            "strike": self.strike,
            "expiration": self.expiration,
            "option_type": self.option_type,
            "delta": self.delta,
            "dte": self.dte,
            "open_interest": self.open_interest,
            "volume": self.volume,
            "bid": self.bid,
            "ask": self.ask,
            "spread_pct": self.spread_pct,
            "vol_oi_ratio": self.vol_oi_ratio,
            "is_unusual_activity": self.is_unusual_activity,
            "curator_score": self.curator_score,
        }


@dataclass
class ScoutResult:
    """
    Result of a Scout scan cycle.
    """
    # Signals detected (sorted by priority desc)
    signals: list[TradeSignal] = field(default_factory=list)

    # Scan metadata
    tickers_scanned: int = 0
    signals_detected: int = 0
    scan_time_ms: float = 0.0
    timestamp: datetime = field(default_factory=datetime.now)

    # Warnings/errors
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Convert to dictionary for logging."""
        return {
            "tickers_scanned": self.tickers_scanned,
            "signals_detected": self.signals_detected,
            "scan_time_ms": self.scan_time_ms,
            "top_signal": self.signals[0].to_dict() if self.signals else None,
            "warnings": self.warnings,
            "timestamp": self.timestamp.isoformat(),
        }


@dataclass
class CuratorResult:
    """
    Result of Curator's option chain scan.
    """
    # Input
    symbol: str
    direction: str  # "call" or "put"

    # Top candidates (sorted by curator_score desc)
    candidates: list[OptionCandidate] = field(default_factory=list)

    # Scan metadata
    total_contracts_scanned: int = 0
    total_passing_filters: int = 0
    scan_time_ms: float = 0.0
    timestamp: datetime = field(default_factory=datetime.now)

    # Warnings/errors
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Convert to dictionary for logging."""
        return {
            "symbol": self.symbol,
            "direction": self.direction,
            "total_contracts_scanned": self.total_contracts_scanned,
            "total_passing_filters": self.total_passing_filters,
            "scan_time_ms": self.scan_time_ms,
            "candidates_count": len(self.candidates),
            "top_candidate": self.candidates[0].to_dict() if self.candidates else None,
            "warnings": self.warnings,
            "timestamp": self.timestamp.isoformat(),
        }
