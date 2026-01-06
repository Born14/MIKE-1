"""
Scout Module for MIKE-1

Signal detection layer - identifies trading opportunities from market data.

Scout's Single Responsibility:
- Scan ticker sources (manual, core, categories)
- Detect catalysts (volume spikes, news, technical setups)
- Create TradeSignal objects for Curator/Judge evaluation

Scout does NOT:
- Select option strikes/expirations (Curator's job)
- Score or grade trades (Judge's job)
- Execute trades (Executor's job)
"""

import time
from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from typing import Optional, List
import uuid

from ..core.trade import TradeSignal, ScoutResult
from ..core.config import Config
from .broker import Broker
from structlog import get_logger

logger = get_logger()


# =============================================================================
# CATALYST PRIORITIES
# =============================================================================

CATALYST_PRIORITIES = {
    "earnings_post": 10,  # Most time-sensitive
    "earnings_pre": 9,
    "news": 8,
    "unusual_options": 7,
    "volume_spike": 5,
    "technical": 4,
}


# =============================================================================
# BASE DETECTOR
# =============================================================================

class BaseDetector(ABC):
    """Base class for all Scout detectors."""

    def __init__(self, config: Config, broker: Broker):
        self.config = config
        self.broker = broker

    @abstractmethod
    def detect(self, ticker: str) -> Optional[TradeSignal]:
        """
        Detect catalyst for a single ticker.

        Returns:
            TradeSignal if catalyst detected, None otherwise
        """
        pass

    def _generate_signal_id(self) -> str:
        """Generate unique signal ID."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        short_uuid = str(uuid.uuid4())[:8]
        return f"sig_{timestamp}_{short_uuid}"


# =============================================================================
# VOLUME SPIKE DETECTOR
# =============================================================================

class VolumeDetector(BaseDetector):
    """Detects unusual volume spikes."""

    def detect(self, ticker: str) -> Optional[TradeSignal]:
        """
        Detect volume spike catalyst.

        Criteria:
        - Volume ≥ 2.5x average
        - Absolute volume > 1M shares
        - Clear direction (price vs VWAP)
        """
        try:
            # Get current stock price
            current_price = self.broker.get_stock_price(ticker)
            if not current_price:
                logger.debug("No price available", ticker=ticker)
                return None

            # Get volume data (current and average)
            volume_data = self.broker.get_volume_data(ticker)
            if not volume_data:
                logger.debug("No volume data available", ticker=ticker)
                return None

            current_volume = volume_data.get("current_volume", 0)
            avg_volume = volume_data.get("avg_volume", 0)

            if not current_volume or not avg_volume:
                return None

            # Check absolute volume minimum
            min_volume = 1_000_000  # 1M shares
            if current_volume < min_volume:
                return None

            # Check volume ratio
            vol_ratio = current_volume / avg_volume
            spike_threshold = 2.5  # 2.5x average
            if vol_ratio < spike_threshold:
                return None

            # Get VWAP to determine direction
            vwap_data = self.broker.get_vwap(ticker)
            if not vwap_data:
                logger.warning("No VWAP data, skipping signal", ticker=ticker)
                return None

            vwap = vwap_data.get("vwap")
            if not vwap:
                return None

            # Determine direction
            if current_price > vwap * 1.001:  # 0.1% above VWAP
                direction = "call"
            elif current_price < vwap * 0.999:  # 0.1% below VWAP
                direction = "put"
            else:
                # Too close to VWAP, no clear direction
                return None

            # Get RSI for additional context
            rsi = self.broker.get_rsi(ticker, period=14)

            # Create signal
            signal = TradeSignal(
                id=self._generate_signal_id(),
                ticker=ticker,
                direction=direction,
                catalyst_type="volume_spike",
                catalyst_description=f"Volume spike {vol_ratio:.1f}x average ({current_volume:,} shares)",
                catalyst_time=datetime.now(),
                current_price=current_price,
                vwap=vwap,
                volume=current_volume,
                avg_volume=avg_volume,
                rsi=rsi,
                priority=CATALYST_PRIORITIES["volume_spike"]
            )

            logger.info(
                "Volume spike detected",
                ticker=ticker,
                direction=direction,
                vol_ratio=vol_ratio,
                volume=current_volume
            )

            return signal

        except Exception as e:
            logger.error("Error detecting volume spike", ticker=ticker, error=str(e))
            return None


# =============================================================================
# MAIN SCOUT CLASS
# =============================================================================

class Scout:
    """
    The Scout scans tickers for catalysts and creates TradeSignals.

    Position in flow: SCOUT → Curator → Judge → Executor
    """

    def __init__(self, broker: Broker, config: Config):
        """
        Initialize Scout.

        Args:
            broker: Broker instance for market data
            config: System configuration
        """
        self.broker = broker
        self.config = config

        # Initialize detectors
        self.detectors = [
            VolumeDetector(config, broker),
            # TODO: Add more detectors (news, technical, UOA)
        ]

        # Cooldown tracking (prevent re-scanning same ticker)
        self.cooldown_tracker = {}  # ticker -> cooldown_until timestamp

    def scan(self) -> ScoutResult:
        """
        Scan all ticker sources and return prioritized signals.

        Returns:
            ScoutResult with detected signals sorted by priority
        """
        start_time = time.time()
        signals = []
        warnings = []

        # Get all tickers from basket (manual + core + categories)
        all_tickers = self.config.basket.all_tickers

        if not all_tickers:
            warnings.append("No tickers in basket")
            logger.warning("No tickers to scan")
            return ScoutResult(
                signals=[],
                tickers_scanned=0,
                signals_detected=0,
                scan_time_ms=0,
                warnings=warnings
            )

        logger.info("Scout scan starting", tickers=len(all_tickers))

        # Scan each ticker
        for ticker in all_tickers:
            # Skip if on cooldown
            if self._is_on_cooldown(ticker):
                logger.debug("Ticker on cooldown, skipping", ticker=ticker)
                continue

            # Run detectors until one finds a signal
            for detector in self.detectors:
                try:
                    signal = detector.detect(ticker)

                    if signal:
                        signals.append(signal)

                        # Set cooldown to prevent immediate re-detection
                        self._set_cooldown(ticker)

                        # Only one signal per ticker per scan
                        break

                except Exception as e:
                    logger.error(
                        "Detector error",
                        ticker=ticker,
                        detector=detector.__class__.__name__,
                        error=str(e)
                    )

        # Sort signals by priority (highest first)
        signals.sort(key=lambda s: s.priority, reverse=True)

        elapsed_ms = (time.time() - start_time) * 1000

        result = ScoutResult(
            signals=signals,
            tickers_scanned=len(all_tickers),
            signals_detected=len(signals),
            scan_time_ms=elapsed_ms,
            warnings=warnings
        )

        logger.info(
            "Scout scan complete",
            tickers_scanned=len(all_tickers),
            signals_detected=len(signals),
            scan_time_ms=elapsed_ms
        )

        return result

    def _is_on_cooldown(self, ticker: str) -> bool:
        """Check if ticker is on cooldown."""
        if ticker not in self.cooldown_tracker:
            return False

        cooldown_until = self.cooldown_tracker[ticker]
        return datetime.now() < cooldown_until

    def _set_cooldown(self, ticker: str, minutes: int = 30):
        """Set cooldown for ticker."""
        cooldown_until = datetime.now() + timedelta(minutes=minutes)
        self.cooldown_tracker[ticker] = cooldown_until
        logger.debug("Cooldown set", ticker=ticker, until=cooldown_until)

    def clear_cooldowns(self):
        """Clear all cooldowns (useful for testing)."""
        self.cooldown_tracker = {}
        logger.info("All cooldowns cleared")
