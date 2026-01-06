"""
Curator Module for MIKE-1

The Curator scans option chains and selects optimal contracts for Judge evaluation.
It bridges Scout (signal detection) and Judge (scoring).

Curator's Single Responsibility:
- Receive signal (ticker + direction) from Scout
- Scan option chain for valid expirations (3-14 DTE)
- Filter contracts by delta, DTE, liquidity
- Rank remaining contracts by 100-point score
- Return top N candidates to Judge
"""

import time
from typing import List, Optional
from datetime import datetime

from ..core.trade import OptionCandidate, CuratorResult, TradeSignal
from ..core.config import Config
from ..utils.dates import get_next_fridays, calculate_dte, filter_expirations_by_dte
from .broker import Broker, OptionQuote
from structlog import get_logger

logger = get_logger()


class Curator:
    """
    The Curator selects optimal option contracts from the chain.

    Position in flow: Scout → CURATOR → Judge → Executor
    """

    def __init__(self, broker: Broker, config: Config):
        """
        Initialize Curator.

        Args:
            broker: Broker instance for fetching option chain data
            config: System configuration
        """
        self.broker = broker
        self.config = config
        self._chain_cache = {}  # Cache chain data to reduce API calls

    def find_best_options(
        self,
        symbol: str,
        direction: str,
        top_n: int = None,
        grade_tier: str = None
    ) -> CuratorResult:
        """
        Scan option chain and return top N candidates.

        Args:
            symbol: Ticker symbol (e.g., "NVDA")
            direction: "call" or "put"
            top_n: Number of candidates to return (default from config)
            grade_tier: Target grade tier "A" or "B" (default: "A")

        Returns:
            CuratorResult with top candidates sorted by curator_score
        """
        start_time = time.time()

        if top_n is None:
            top_n = self.config.curator.max_candidates
        if grade_tier is None:
            grade_tier = "A"

        logger.info("Curator scanning chain",
                   symbol=symbol,
                   direction=direction,
                   top_n=top_n,
                   grade_tier=grade_tier)

        result = CuratorResult(
            symbol=symbol,
            direction=direction
        )

        # Get valid expiration dates (within DTE range)
        expirations = self._get_valid_expirations()

        if not expirations:
            result.warnings.append("No valid expirations found in DTE range")
            result.scan_time_ms = (time.time() - start_time) * 1000
            logger.warning("No valid expirations",
                          min_dte=self.config.options.min_dte,
                          max_dte=self.config.options.max_dte)
            return result

        logger.info("Scanning expirations",
                   count=len(expirations),
                   expirations=expirations)

        # Get current stock price (for ATM proximity)
        stock_price = self.broker.get_stock_price(symbol)
        if not stock_price:
            result.warnings.append(f"Could not get stock price for {symbol}")
            result.scan_time_ms = (time.time() - start_time) * 1000
            return result

        # Scan all valid expirations
        all_candidates = []
        for expiration in expirations:
            chain = self._get_chain(symbol, expiration, direction)
            result.total_contracts_scanned += len(chain)

            for quote in chain:
                # Filter by hard constraints
                if self._passes_filters(quote, grade_tier):
                    candidate = self._convert_to_candidate(
                        quote,
                        stock_price,
                        grade_tier
                    )
                    all_candidates.append(candidate)
                    result.total_passing_filters += 1

        if not all_candidates:
            result.warnings.append(
                f"No contracts passed filters (scanned {result.total_contracts_scanned})"
            )
            result.scan_time_ms = (time.time() - start_time) * 1000
            logger.warning("No candidates passed filters",
                          symbol=symbol,
                          scanned=result.total_contracts_scanned)
            return result

        # Sort by curator_score (highest first)
        all_candidates.sort(key=lambda c: c.curator_score, reverse=True)

        # Return top N
        result.candidates = all_candidates[:top_n]
        result.scan_time_ms = (time.time() - start_time) * 1000

        logger.info("Curator scan complete",
                   symbol=symbol,
                   scanned=result.total_contracts_scanned,
                   passed_filters=result.total_passing_filters,
                   candidates_returned=len(result.candidates),
                   scan_time_ms=result.scan_time_ms,
                   top_score=result.candidates[0].curator_score if result.candidates else 0)

        return result

    def _get_valid_expirations(self) -> List[str]:
        """
        Get valid expiration dates within DTE range.

        Returns:
            List of YYYY-MM-DD strings for valid expirations
        """
        # Calculate next 4 Fridays (covers up to ~28 DTE)
        all_fridays = get_next_fridays(count=4)

        # Filter to min_dte:max_dte range
        valid = filter_expirations_by_dte(
            all_fridays,
            self.config.options.min_dte,
            self.config.options.max_dte
        )

        return valid

    def _get_chain(
        self,
        symbol: str,
        expiration: str,
        direction: str
    ) -> List[OptionQuote]:
        """
        Get option chain for a specific expiration.

        Uses cache to reduce API calls.

        Args:
            symbol: Ticker symbol
            expiration: Expiration date (YYYY-MM-DD)
            direction: "call" or "put"

        Returns:
            List of OptionQuote objects
        """
        # Build cache key
        cache_key = f"{symbol}:{expiration}:{direction}"

        # Check cache
        cache_seconds = self.config.curator.cache_chain_seconds
        if cache_key in self._chain_cache:
            cached_data, cached_time = self._chain_cache[cache_key]
            age_seconds = (time.time() - cached_time)
            if age_seconds < cache_seconds:
                logger.debug("Using cached chain data",
                            symbol=symbol,
                            expiration=expiration,
                            age_seconds=age_seconds)
                return cached_data

        # Fetch from broker
        chain = self.broker.get_option_chain(symbol, expiration, direction)

        # Cache it
        self._chain_cache[cache_key] = (chain, time.time())

        logger.debug("Fetched chain from broker",
                    symbol=symbol,
                    expiration=expiration,
                    contracts=len(chain))

        return chain

    def _passes_filters(self, quote: OptionQuote, grade_tier: str) -> bool:
        """
        Check if option passes hard filters.

        Args:
            quote: Option quote to check
            grade_tier: Target grade tier ("A" or "B")

        Returns:
            True if passes all filters
        """
        # Get delta range for grade tier
        if grade_tier == "A":
            delta_min = self.config.options.a_tier.delta_min
            delta_max = self.config.options.a_tier.delta_max
        elif grade_tier == "B":
            delta_min = self.config.options.b_tier.delta_min
            delta_max = self.config.options.b_tier.delta_max
        else:
            logger.error("Invalid grade tier", tier=grade_tier)
            return False

        # Filter 1: Delta range
        if not (delta_min <= abs(quote.delta) <= delta_max):
            return False

        # Filter 2: DTE range
        dte = calculate_dte(quote.expiration)
        if not (self.config.options.min_dte <= dte <= self.config.options.max_dte):
            return False

        # Filter 3: Open interest minimum
        if quote.open_interest < self.config.options.min_open_interest:
            return False

        # Filter 4: Bid-ask spread
        if quote.ask <= 0:
            return False  # Can't calculate spread
        spread_pct = (quote.ask - quote.bid) / quote.ask
        if spread_pct > self.config.options.max_bid_ask_spread_pct:
            return False

        return True

    def _convert_to_candidate(
        self,
        quote: OptionQuote,
        stock_price: float,
        grade_tier: str
    ) -> OptionCandidate:
        """
        Convert OptionQuote to OptionCandidate with ranking score.

        Args:
            quote: Option quote from broker
            stock_price: Current underlying price
            grade_tier: Target grade tier

        Returns:
            OptionCandidate with curator_score calculated
        """
        # Calculate DTE
        dte = calculate_dte(quote.expiration)

        # Calculate spread %
        spread_pct = (quote.ask - quote.bid) / quote.ask if quote.ask > 0 else 1.0

        # Calculate vol/OI ratio
        vol_oi_ratio = quote.volume / quote.open_interest if quote.open_interest > 0 else 0

        # Detect unusual activity
        unusual_threshold = 1.25  # TODO: Move to config
        is_unusual = vol_oi_ratio >= unusual_threshold

        # Create candidate
        candidate = OptionCandidate(
            symbol=quote.symbol,
            strike=quote.strike,
            expiration=quote.expiration,
            option_type=quote.option_type,
            delta=quote.delta,
            dte=dte,
            open_interest=quote.open_interest,
            volume=quote.volume,
            bid=quote.bid,
            ask=quote.ask,
            spread_pct=spread_pct,
            vol_oi_ratio=vol_oi_ratio,
            is_unusual_activity=is_unusual
        )

        # Calculate ranking score
        score, reasons = self._rank_candidate(candidate, stock_price, grade_tier)
        candidate.curator_score = score
        candidate.ranking_reasons = reasons

        return candidate

    def _rank_candidate(
        self,
        candidate: OptionCandidate,
        stock_price: float,
        grade_tier: str
    ) -> tuple[float, List[str]]:
        """
        Score candidate 0-100 (higher = better for Judge evaluation).

        Scoring weights:
        - Delta Proximity: 30 points
        - Liquidity: 30 points (OI + spread)
        - Unusual Activity: 20 points
        - ATM Proximity: 20 points

        Args:
            candidate: Option candidate to rank
            stock_price: Current underlying price
            grade_tier: Target grade tier

        Returns:
            Tuple of (score, reasons)
        """
        score = 0.0
        reasons = []

        # 1. Delta Proximity (30 points)
        # Ideal delta is midpoint of range
        if grade_tier == "A":
            ideal_delta = (self.config.options.a_tier.delta_min +
                          self.config.options.a_tier.delta_max) / 2
        else:
            ideal_delta = (self.config.options.b_tier.delta_min +
                          self.config.options.b_tier.delta_max) / 2

        delta_distance = abs(abs(candidate.delta) - ideal_delta)
        delta_score = max(0, 30 - delta_distance * 100)
        score += delta_score
        reasons.append(f"Δ {abs(candidate.delta):.2f} ({delta_score:.0f}pts)")

        # 2. Liquidity (30 points total)
        # - OI: 0-15 points
        # - Spread: 0-15 points

        # OI score: Linear scaling, caps at 15pts for OI >= 1500
        oi_score = min(15, (candidate.open_interest / 100))
        score += oi_score
        reasons.append(f"OI {candidate.open_interest:,} ({oi_score:.0f}pts)")

        # Spread score: Tighter spread = higher score
        # 0% spread = 15pts, 10% spread = 0pts
        spread_score = max(0, 15 - (candidate.spread_pct * 150))
        score += spread_score
        reasons.append(f"Spread {candidate.spread_pct*100:.1f}% ({spread_score:.0f}pts)")

        # 3. Unusual Activity (20 points)
        if candidate.is_unusual_activity:
            uoa_boost = self.config.curator.unusual_activity_boost
            score += uoa_boost
            reasons.append(f"🔥 UOA {candidate.vol_oi_ratio:.2f}x (+{uoa_boost:.0f}pts)")

        # 4. ATM Proximity (20 points)
        # Closer to current price = better gamma/liquidity
        moneyness = candidate.strike / stock_price
        atm_distance = abs(moneyness - 1.0)  # 0 = ATM, >0 = OTM/ITM
        atm_score = max(0, 20 - atm_distance * 50)
        score += atm_score
        reasons.append(f"Moneyness {moneyness:.2f} ({atm_score:.0f}pts)")

        return score, reasons
