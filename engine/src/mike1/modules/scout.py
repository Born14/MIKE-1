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
from .llm_client import get_llm_client
from .social import get_social_client
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
# NEWS DETECTOR
# =============================================================================

class NewsDetector(BaseDetector):
    """Detects news-driven catalysts using social data + LLM."""

    def __init__(self, config: Config, broker: Broker):
        super().__init__(config, broker)
        self.social_client = get_social_client()
        self.llm_client = get_llm_client()

    def detect(self, ticker: str) -> Optional[TradeSignal]:
        """
        Detect news catalyst.

        Criteria:
        - Recent news/social mentions (>10 mentions)
        - LLM confirms catalyst is significant
        - Clear sentiment direction
        """
        try:
            # Get social data
            social_data = self.social_client.get_social_data(ticker)

            if social_data.total_mentions < 10:
                # Not enough chatter
                return None

            # Get current price for context
            current_price = self.broker.get_stock_price(ticker)
            if not current_price:
                return None

            # Build LLM prompt
            news_snippets = []

            # Include StockTwits messages
            for msg in social_data.stocktwits_messages[:5]:
                news_snippets.append(f"StockTwits: {msg.get('body', '')}")

            # Include Reddit posts
            for post in social_data.reddit_posts[:3]:
                news_snippets.append(f"Reddit: {post.get('title', '')}")

            # Include Alpha Vantage articles
            for article in social_data.alphavantage_articles[:3]:
                news_snippets.append(f"News: {article.get('title', '')}")

            if not news_snippets:
                return None

            # Ask LLM to assess
            prompt = f"""Analyze this social/news data for {ticker} (current price: ${current_price:.2f}):

{chr(10).join(news_snippets[:10])}

Is there a significant catalyst that would drive options trading? Consider:
- Earnings announcements
- Product launches
- Regulatory news
- Major partnerships
- Analyst upgrades/downgrades
- Unusual market action

Ignore: General market commentary, technical analysis posts, casual mentions."""

            if not self.llm_client:
                # No LLM available, fall back to sentiment only
                if social_data.is_trending:
                    direction = "call" if social_data.overall_sentiment == "bullish" else "put"
                    return TradeSignal(
                        id=self._generate_signal_id(),
                        ticker=ticker,
                        direction=direction,
                        catalyst_type="news",
                        catalyst_description=f"Trending: {social_data.total_mentions} mentions ({social_data.overall_sentiment})",
                        catalyst_time=datetime.now(),
                        current_price=current_price,
                        priority=CATALYST_PRIORITIES["news"]
                    )
                return None

            # Use LLM
            assessment = self.llm_client.assess_catalyst(prompt)
            if not assessment or not assessment.get("has_catalyst"):
                return None

            # Determine direction from LLM sentiment
            sentiment = assessment.get("sentiment", "neutral")
            if sentiment == "bullish":
                direction = "call"
            elif sentiment == "bearish":
                direction = "put"
            else:
                return None  # No clear direction

            # Create signal
            signal = TradeSignal(
                id=self._generate_signal_id(),
                ticker=ticker,
                direction=direction,
                catalyst_type="news",
                catalyst_description=assessment.get("summary", "News catalyst detected"),
                catalyst_time=datetime.now(),
                current_price=current_price,
                priority=CATALYST_PRIORITIES["news"]
            )

            logger.info(
                "News catalyst detected",
                ticker=ticker,
                direction=direction,
                mentions=social_data.total_mentions,
                sentiment=sentiment,
                confidence=assessment.get("confidence")
            )

            return signal

        except Exception as e:
            logger.error("Error detecting news", ticker=ticker, error=str(e))
            return None


# =============================================================================
# TECHNICAL DETECTOR
# =============================================================================

class TechnicalDetector(BaseDetector):
    """Detects technical setups (RSI extremes, VWAP reversals)."""

    def detect(self, ticker: str) -> Optional[TradeSignal]:
        """
        Detect technical catalyst.

        Criteria:
        - RSI <30 (oversold) or >70 (overbought)
        - Price crossing VWAP with volume confirmation
        """
        try:
            # Get price and RSI
            current_price = self.broker.get_stock_price(ticker)
            if not current_price:
                return None

            rsi = self.broker.get_rsi(ticker, period=14)
            if not rsi:
                return None

            # RSI extreme checks
            if rsi <= 30:
                # Oversold - potential bounce (call)
                direction = "call"
                description = f"RSI oversold at {rsi:.1f}"
            elif rsi >= 70:
                # Overbought - potential pullback (put)
                direction = "put"
                description = f"RSI overbought at {rsi:.1f}"
            else:
                # No RSI extreme
                return None

            # Get VWAP for additional context
            vwap_data = self.broker.get_vwap(ticker)
            vwap = vwap_data.get("vwap") if vwap_data else None

            # Get volume for confirmation
            volume_data = self.broker.get_volume_data(ticker)
            current_volume = volume_data.get("current_volume") if volume_data else 0
            avg_volume = volume_data.get("avg_volume") if volume_data else 0

            # Create signal
            signal = TradeSignal(
                id=self._generate_signal_id(),
                ticker=ticker,
                direction=direction,
                catalyst_type="technical",
                catalyst_description=description,
                catalyst_time=datetime.now(),
                current_price=current_price,
                vwap=vwap,
                volume=current_volume,
                avg_volume=avg_volume,
                rsi=rsi,
                priority=CATALYST_PRIORITIES["technical"]
            )

            logger.info(
                "Technical setup detected",
                ticker=ticker,
                direction=direction,
                rsi=rsi,
                description=description
            )

            return signal

        except Exception as e:
            logger.error("Error detecting technical", ticker=ticker, error=str(e))
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

        # Initialize detectors (in priority order)
        self.detectors = [
            NewsDetector(config, broker),      # Priority 8 (high)
            VolumeDetector(config, broker),    # Priority 5 (medium)
            TechnicalDetector(config, broker), # Priority 4 (low)
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
