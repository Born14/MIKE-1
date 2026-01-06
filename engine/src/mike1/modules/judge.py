"""
Judge Module for MIKE-1

The Judge grades trade candidates on a weighted scoring system.
It does NOT decide whether to trade - that's Executor's job.
Judge just provides an objective assessment.

Scoring Factors:
- Technical: Volume spike, VWAP alignment, RSI not extreme
- Liquidity: Open interest, bid-ask spread
- Catalyst: LLM-assessed news/sentiment (optional)

Output: Grade (A/B/NO_TRADE) + Score (0-10) + Reasoning
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
import structlog

from ..core.config import get_config
from ..core.scouters_rubric import ScoringRubric
from ..core.trade import TradeGrade  # Consolidated enum

logger = structlog.get_logger()


@dataclass
class TechnicalData:
    """Technical indicators for a ticker."""
    symbol: str
    current_price: float = 0

    # Volume
    current_volume: int = 0
    avg_volume_20d: int = 0
    volume_ratio: float = 0  # current / avg

    # VWAP
    vwap: float = 0
    price_vs_vwap: float = 0  # positive = above, negative = below

    # RSI
    rsi_14: float = 50

    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class LiquidityData:
    """Liquidity metrics for an option."""
    symbol: str
    strike: float
    expiration: str
    option_type: str

    open_interest: int = 0
    volume: int = 0
    bid: float = 0
    ask: float = 0
    spread: float = 0  # ask - bid
    spread_pct: float = 0  # spread / mid price

    # Delta for position sizing/scoring
    delta: float = 0
    
    # Days to expiration
    dte: int = 0

    # Unusual activity detection (Vol/OI > 1.25 = unusual per Barchart)
    vol_oi_ratio: float = 0
    is_unusual_activity: bool = False


@dataclass
class CatalystData:
    """LLM-assessed catalyst/sentiment data."""
    has_catalyst: bool = False
    catalyst_summary: str = ""
    sentiment: str = "neutral"  # bullish, bearish, neutral
    mention_type: str = "passing"  # primary, secondary, passing
    confidence: float = 0  # 0-1
    reasoning: str = ""

    # Raw data passed to LLM
    headlines: list[str] = field(default_factory=list)

    # Social media data
    social_sentiment: str = "neutral"
    social_volume: int = 0
    social_bullish_pct: float = 50
    social_messages: list[str] = field(default_factory=list)  # Sample messages

    # Reddit data
    reddit_sentiment: str = "neutral"
    reddit_volume: int = 0
    reddit_bullish_pct: float = 50
    reddit_posts: list[str] = field(default_factory=list)  # Sample post titles


@dataclass
class JudgeVerdict:
    """
    Complete assessment of a trade candidate.

    This is what Judge returns - grade, score, and reasoning.
    """
    symbol: str
    direction: str  # "call" or "put"
    grade: TradeGrade
    score: float  # 0-10

    # Factor scores (each 0-10)
    technical_score: float = 0
    liquidity_score: float = 0
    catalyst_score: float = 0

    # Weights used
    weights: dict = field(default_factory=dict)

    # Reasoning
    reasoning: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    # Raw data
    technical: Optional[TechnicalData] = None
    liquidity: Optional[LiquidityData] = None
    catalyst: Optional[CatalystData] = None

    # Metadata
    timestamp: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> dict:
        """Convert to dictionary for logging."""
        return {
            "symbol": self.symbol,
            "direction": self.direction,
            "grade": self.grade.value,
            "score": round(self.score, 1),
            "technical_score": round(self.technical_score, 1),
            "liquidity_score": round(self.liquidity_score, 1),
            "catalyst_score": round(self.catalyst_score, 1),
            "reasoning": self.reasoning,
            "warnings": self.warnings,
            "timestamp": self.timestamp.isoformat()
        }


class Judge:
    """
    The Judge - grades trade candidates objectively.

    Usage:
        judge = Judge(broker)
        verdict = judge.grade("NVDA", "call")
        print(f"{verdict.symbol}: {verdict.grade.value} ({verdict.score}/10)")
    """

    # Scoring weights (must sum to 1.0)
    WEIGHTS = {
        "technical": 0.35,   # Volume, VWAP, RSI
        "liquidity": 0.35,   # OI, spread, delta, DTE
        "catalyst": 0.30,    # News/sentiment (LLM)
    }

    # Thresholds
    A_TIER_MIN = 7.0   # Score >= 7 for A-tier
    B_TIER_MIN = 5.0   # Score >= 5 for B-tier

    def __init__(self, broker, llm_client=None):
        """
        Initialize Judge.

        Args:
            broker: Broker instance for market data
            llm_client: Optional LLM client for catalyst scoring
        """
        self.broker = broker
        self.llm_client = llm_client
        self.config = get_config()

    def grade(
        self,
        symbol: str,
        direction: str,
        strike: Optional[float] = None,
        expiration: Optional[str] = None,
        use_llm: bool = True
    ) -> JudgeVerdict:
        """
        Grade a trade candidate.

        Args:
            symbol: Ticker symbol (e.g., "NVDA")
            direction: "call" or "put"
            strike: Optional strike price (for liquidity check)
            expiration: Optional expiration date (for liquidity check)

        Returns:
            JudgeVerdict with grade, score, and reasoning
        """
        logger.info("Judge evaluating", symbol=symbol, direction=direction)

        reasoning = []
        warnings = []

        # 1. Get technical data
        technical = self._get_technical_data(symbol)
        tech_score, tech_reasons = self._score_technical(technical, direction)
        reasoning.extend(tech_reasons)

        # 2. Get liquidity data (if strike/expiration provided)
        liquidity = None
        liq_score = 5.0  # Default neutral if no option specified
        if strike and expiration:
            liquidity = self._get_liquidity_data(symbol, strike, expiration, direction)
            liq_score, liq_reasons = self._score_liquidity(liquidity)
            reasoning.extend(liq_reasons)
        else:
            warnings.append("No strike/expiration - liquidity/delta/DTE not scored")

        # 3. Get catalyst data (if LLM available and enabled)
        catalyst = None
        cat_score = 5.0  # Default neutral if no LLM
        if self.llm_client and use_llm:
            catalyst = self._get_catalyst_data(symbol, direction)
            cat_score, cat_reasons = self._score_catalyst(catalyst)
            reasoning.extend(cat_reasons)
        else:
            # If we don't have an LLM, we can't score catalyst
            # For backtesting/dev without LLM keys, maybe we want this to be neutral (5.0)
            # but warn about it.
            warnings.append("No LLM client - catalyst not scored (defaulting to neutral 5.0)")

        # 4. Calculate weighted score
        score = (
            tech_score * self.WEIGHTS["technical"] +
            liq_score * self.WEIGHTS["liquidity"] +
            cat_score * self.WEIGHTS["catalyst"]
        )

        # 5. Determine grade
        if score >= self.A_TIER_MIN:
            grade = TradeGrade.A_TIER
        elif score >= self.B_TIER_MIN:
            grade = TradeGrade.B_TIER
        else:
            grade = TradeGrade.NO_TRADE

        verdict = JudgeVerdict(
            symbol=symbol,
            direction=direction,
            grade=grade,
            score=score,
            technical_score=tech_score,
            liquidity_score=liq_score,
            catalyst_score=cat_score,
            weights=self.WEIGHTS,
            reasoning=reasoning,
            warnings=warnings,
            technical=technical,
            liquidity=liquidity,
            catalyst=catalyst
        )

        logger.info(
            "Judge verdict",
            symbol=symbol,
            grade=grade.value,
            score=f"{score:.1f}/10",
            tech=f"{tech_score:.1f}",
            liq=f"{liq_score:.1f}",
            cat=f"{cat_score:.1f}"
        )

        return verdict

    def _get_technical_data(self, symbol: str) -> TechnicalData:
        """Fetch technical indicators from broker."""
        data = TechnicalData(symbol=symbol)

        try:
            # Get current price
            data.current_price = self.broker.get_stock_price(symbol)

            # Get volume data
            volume_data = self.broker.get_volume_data(symbol)
            if volume_data:
                data.current_volume = volume_data.get("current_volume", 0)
                data.avg_volume_20d = volume_data.get("avg_volume", 0)
                if data.avg_volume_20d > 0:
                    data.volume_ratio = data.current_volume / data.avg_volume_20d

            # Get VWAP
            vwap_data = self.broker.get_vwap(symbol)
            if vwap_data:
                data.vwap = vwap_data.get("vwap", 0)
                if data.vwap > 0:
                    data.price_vs_vwap = ((data.current_price - data.vwap) / data.vwap) * 100

            # Get RSI
            data.rsi_14 = self.broker.get_rsi(symbol, period=14)

        except Exception as e:
            logger.error("Error fetching technical data", symbol=symbol, error=str(e))

        return data

    def _get_liquidity_data(
        self,
        symbol: str,
        strike: float,
        expiration: str,
        direction: str
    ) -> LiquidityData:
        """Fetch option liquidity data from broker."""
        data = LiquidityData(
            symbol=symbol,
            strike=strike,
            expiration=expiration,
            option_type=direction
        )

        try:
            quote = self.broker.get_option_quote(symbol, strike, expiration, direction)
            if quote:
                data.open_interest = quote.open_interest
                data.volume = quote.volume
                data.bid = quote.bid
                data.ask = quote.ask
                data.spread = quote.ask - quote.bid
                mid = (quote.bid + quote.ask) / 2
                if mid > 0:
                    data.spread_pct = (data.spread / mid) * 100
                data.delta = abs(quote.delta)
                
                # Calculate DTE
                try:
                    exp_date = datetime.strptime(expiration, "%Y-%m-%d")
                    # DTE = (exp_date - now).days
                    # We can use dates.get_dte if available, but simplistic approach here:
                    now = datetime.now()
                    # Reset times for purely day comparison
                    dte = (exp_date.date() - now.date()).days
                    data.dte = dte
                except ValueError:
                    logger.warning("Could not parse expiration", expiration=expiration)
                    data.dte = 0

                # Calculate Vol/OI ratio for unusual activity detection
                # Barchart threshold: Vol/OI > 1.25 = unusual
                if data.open_interest > 100 and data.volume > 500:
                    data.vol_oi_ratio = data.volume / data.open_interest
                    data.is_unusual_activity = data.vol_oi_ratio >= 1.25

        except Exception as e:
            logger.error("Error fetching liquidity data", symbol=symbol, error=str(e))

        return data

    def _get_catalyst_data(self, symbol: str, direction: str) -> CatalystData:
        """
        Get catalyst/sentiment data via LLM.

        This calls the LLM with recent news AND social media data.
        """
        data = CatalystData()

        if not self.llm_client:
            return data

        try:
            # Get recent headlines (broker.get_news if available)
            headlines = []
            if hasattr(self.broker, 'get_news'):
                news = self.broker.get_news(symbol, limit=5)
                headlines = [n.get("headline", "") for n in news]

            data.headlines = headlines

            # Get social media data (StockTwits + Reddit)
            # Try/Except block to gracefully handle missing social module
            try:
                from .social import get_social_client
                social_client = get_social_client()
                social_data = social_client.get_social_data(symbol)

                # StockTwits
                data.social_sentiment = social_data.stocktwits_sentiment
                data.social_volume = social_data.stocktwits_volume
                data.social_bullish_pct = social_data.stocktwits_bullish_pct

                # Get sample messages for LLM context (top 5 most liked)
                sorted_msgs = sorted(
                    social_data.stocktwits_messages,
                    key=lambda x: x.get("likes", 0),
                    reverse=True
                )[:5]
                data.social_messages = [m.get("body", "") for m in sorted_msgs]

                # Reddit
                data.reddit_sentiment = social_data.reddit_sentiment
                data.reddit_volume = social_data.reddit_volume
                data.reddit_bullish_pct = social_data.reddit_bullish_pct

                # Get sample Reddit post titles (top by score)
                sorted_posts = sorted(
                    social_data.reddit_posts,
                    key=lambda x: x.get("score", 0),
                    reverse=True
                )[:5]
                data.reddit_posts = [p.get("title", "") for p in sorted_posts]

            except ImportError:
                # social.py not fully implemented yet in previous context, so we skip
                # This prevents hard crash if file is missing
                pass
            except Exception as e:
                logger.debug("Social data unavailable", error=str(e))

            if not headlines and not data.social_messages:
                data.reasoning = "No recent news or social data found"
                return data

            # Call LLM for assessment with both news and social
            prompt = self._build_catalyst_prompt(symbol, direction, headlines, data)
            
            response = self.llm_client.assess_catalyst(prompt)

            if response:
                data.has_catalyst = response.get("has_catalyst", False)
                data.catalyst_summary = response.get("summary", "")
                data.sentiment = response.get("sentiment", "neutral")
                data.mention_type = response.get("mention_type", "passing")
                data.confidence = response.get("confidence", 0)
                data.reasoning = response.get("reasoning", "")

        except Exception as e:
            logger.error("Error fetching catalyst data", symbol=symbol, error=str(e))

        return data

    def _build_catalyst_prompt(
        self,
        symbol: str,
        direction: str,
        headlines: list[str],
        catalyst_data: Optional[CatalystData] = None
    ) -> str:
        """Build prompt for LLM catalyst assessment."""
        direction_word = "bullish" if direction == "call" else "bearish"

        # Build news section
        news_section = ""
        if headlines:
            news_section = f"""
Recent news headlines:
{chr(10).join(f"- {h}" for h in headlines)}
"""

        # Build social section (StockTwits + Reddit)
        social_section = ""
        social_parts = []

        if catalyst_data:
            # StockTwits
            if catalyst_data.social_messages:
                social_parts.append(f"""StockTwits ({catalyst_data.social_volume} msgs, {catalyst_data.social_bullish_pct:.0f}% bullish):
{chr(10).join(f"- {m[:150]}" for m in catalyst_data.social_messages[:3] if m)}""")
            elif catalyst_data.social_volume > 0:
                social_parts.append(f"StockTwits: {catalyst_data.social_volume} msgs, {catalyst_data.social_bullish_pct:.0f}% bullish")

            # Reddit
            if catalyst_data.reddit_posts:
                social_parts.append(f"""Reddit/WSB ({catalyst_data.reddit_volume} posts, {catalyst_data.reddit_bullish_pct:.0f}% bullish):
{chr(10).join(f"- {p[:150]}" for p in catalyst_data.reddit_posts[:3] if p)}""")
            elif catalyst_data.reddit_volume > 0:
                social_parts.append(f"Reddit: {catalyst_data.reddit_volume} posts, {catalyst_data.reddit_bullish_pct:.0f}% bullish")

        if social_parts:
            social_section = "\n" + "\n\n".join(social_parts) + "\n"

        return f"""Assess the following data for {symbol}.

I'm considering a {direction} option trade ({direction_word} thesis).
{news_section}{social_section}
Questions:
1. Is there a meaningful catalyst in the news OR unusual social activity?
2. Is {symbol} the PRIMARY SUBJECT of the news, or just mentioned?
   - Primary = news is ABOUT this company specifically
   - Secondary = mentioned as partner/peer/comparison
   - Passing = just listed among many tickers OR only social chatter
3. Does the overall sentiment support a {direction_word} thesis?
4. Is the social volume/sentiment significant?

IMPORTANT: Weight your confidence based on data quality:
- Primary news catalyst + aligned social sentiment: confidence 0.8-1.0
- Primary news catalyst, mixed/no social: confidence 0.6-0.8
- Secondary mention + strong social sentiment: confidence 0.4-0.6
- Only social chatter, no real news: confidence 0.2-0.4
- Passing mention, weak social: confidence 0.1-0.2
- No catalyst or data: confidence 0

Respond with:
- has_catalyst: true/false
- mention_type: "primary", "secondary", or "passing"
- sentiment: bullish/bearish/neutral
- confidence: 0-1 (weighted as described above)
- summary: One sentence summary combining news + social sentiment
- reasoning: Why this data supports or contradicts the {direction_word} thesis
"""

    def _score_technical(
        self,
        data: TechnicalData,
        direction: str
    ) -> tuple[float, list[str]]:
        """
        Score technical factors using centralized rubric.
        """
        result = ScoringRubric.score_technicals(
            vol_ratio=data.volume_ratio,
            price_vs_vwap_pct=data.price_vs_vwap,
            rsi=data.rsi_14,
            direction=direction
        )
        return result.score, result.reasons

    def _score_liquidity(self, data: LiquidityData) -> tuple[float, list[str]]:
        """
        Score liquidity factors, including Delta and DTE.
        """
        # 1. Base Liquidity Score (OI, Spread, UOA)
        liq_result = ScoringRubric.score_liquidity(
            oi=data.open_interest,
            spread_pct=data.spread_pct,
            vol_oi_ratio=data.vol_oi_ratio,
            is_unusual=data.is_unusual_activity,
            min_oi=self.config.options.min_open_interest,
            max_spread=self.config.options.max_bid_ask_spread_pct * 100
        )
        
        score = liq_result.score
        reasons = liq_result.reasons
        
        # 2. Add Delta Score (New)
        delta_result = ScoringRubric.score_delta(data.delta)
        score += delta_result.score
        reasons.extend(delta_result.reasons)
        
        # 3. Add DTE Score (New)
        dte_result = ScoringRubric.score_dte(data.dte)
        score += dte_result.score
        reasons.extend(dte_result.reasons)
        
        # Clamp Score
        score = max(0, min(10, score))
        
        return score, reasons

    def _score_catalyst(self, data: CatalystData) -> tuple[float, list[str]]:
        """
        Score catalyst/sentiment factors.

        Returns score (0-10) and list of reasoning strings.
        """
        score = 5.0  # Start neutral
        reasons = []

        if not data.has_catalyst:
            reasons.append("No significant catalyst detected")
            return score, reasons

        # Has catalyst (+2 base)
        score += 2
        reasons.append(f"Catalyst: {data.catalyst_summary}")

        # Sentiment alignment (0-3 points)
        # Note: direction matching handled by confidence
        if data.confidence >= 0.8:
            score += 3
            reasons.append(f"High confidence {data.sentiment} sentiment")
        elif data.confidence >= 0.5:
            score += 1
            reasons.append(f"Moderate confidence {data.sentiment} sentiment")

        # Add LLM reasoning if available
        if data.reasoning:
            reasons.append(f"LLM: {data.reasoning}")

        # Clamp score
        score = max(0, min(10, score))

        return score, reasons

    def explain(self, verdict: JudgeVerdict) -> str:
        """
        Generate human-readable explanation of verdict.

        Useful for CLI output or logging.
        """
        lines = [
            f"=== JUDGE VERDICT: {verdict.symbol} {verdict.direction.upper()} ===",
            f"Grade: {verdict.grade.value}-TIER",
            f"Score: {verdict.score:.1f}/10",
            "",
            f"Technical:  {verdict.technical_score:.1f}/10 (weight: {self.WEIGHTS['technical']*100:.0f}%)",
            f"Liquidity:  {verdict.liquidity_score:.1f}/10 (weight: {self.WEIGHTS['liquidity']*100:.0f}%)",
            f"Catalyst:   {verdict.catalyst_score:.1f}/10 (weight: {self.WEIGHTS['catalyst']*100:.0f}%)",
            "",
            "Reasoning:"
        ]

        for reason in verdict.reasoning:
            lines.append(f"  - {reason}")

        if verdict.warnings:
            lines.append("")
            lines.append("Warnings:")
            for warning in verdict.warnings:
                lines.append(f"  ! {warning}")

        return "\n".join(lines)
