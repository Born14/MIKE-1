"""
Social Sentiment Data for MIKE-1

Fetches social media chatter for Judge to assess.
Currently supports:
- StockTwits (free, no API key required)
- Reddit (r/wallstreetbets, r/options) - free, rate limited
- Alpha Vantage News Sentiment (free, 25 req/day, requires API key)

Not supported:
- Twitter/X (requires $100+/mo paid API)
- Yahoo Finance message boards (no API)
- Seeking Alpha (paid API only)
"""

import os
import requests
import time
from typing import Optional
from dataclasses import dataclass, field
from datetime import datetime
import structlog

logger = structlog.get_logger()


@dataclass
class SocialData:
    """Aggregated social sentiment data."""
    symbol: str

    # StockTwits
    stocktwits_messages: list[dict] = field(default_factory=list)
    stocktwits_sentiment: str = "neutral"  # bullish, bearish, neutral
    stocktwits_bullish_pct: float = 0
    stocktwits_volume: int = 0  # Number of messages

    # Reddit
    reddit_posts: list[dict] = field(default_factory=list)
    reddit_sentiment: str = "neutral"
    reddit_bullish_pct: float = 50
    reddit_volume: int = 0

    # Alpha Vantage News Sentiment
    alphavantage_articles: list[dict] = field(default_factory=list)
    alphavantage_sentiment: str = "neutral"
    alphavantage_score: float = 0  # -1 to 1
    alphavantage_volume: int = 0

    # Aggregated
    total_mentions: int = 0
    overall_sentiment: str = "neutral"
    is_trending: bool = False

    timestamp: datetime = field(default_factory=datetime.now)


class SocialClient:
    """
    Fetches social media data for sentiment analysis.
    """

    STOCKTWITS_BASE = "https://api.stocktwits.com/api/2"
    REDDIT_BASE = "https://www.reddit.com"
    ALPHAVANTAGE_BASE = "https://www.alphavantage.co/query"

    # Subreddits to search for stock mentions
    TRADING_SUBREDDITS = ["wallstreetbets", "options", "stocks", "investing"]

    # Rate limiting
    _last_reddit_call = 0
    REDDIT_RATE_LIMIT = 1.0  # seconds between calls

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "MIKE-1 Trading System/1.0"
        })
        self.alphavantage_key = os.environ.get("ALPHAVANTAGE_API_KEY")

    def get_social_data(self, symbol: str) -> SocialData:
        """
        Get aggregated social data for a symbol.

        Args:
            symbol: Ticker symbol (e.g., "NVDA")

        Returns:
            SocialData with messages and sentiment
        """
        data = SocialData(symbol=symbol)

        # Get StockTwits data
        st_data = self._get_stocktwits(symbol)
        if st_data:
            data.stocktwits_messages = st_data.get("messages", [])
            data.stocktwits_sentiment = st_data.get("sentiment", "neutral")
            data.stocktwits_bullish_pct = st_data.get("bullish_pct", 50)
            data.stocktwits_volume = len(data.stocktwits_messages)

        # Get Reddit data
        reddit_data = self._get_reddit(symbol)
        if reddit_data:
            data.reddit_posts = reddit_data.get("posts", [])
            data.reddit_sentiment = reddit_data.get("sentiment", "neutral")
            data.reddit_bullish_pct = reddit_data.get("bullish_pct", 50)
            data.reddit_volume = len(data.reddit_posts)

        # Get Alpha Vantage news sentiment (if API key configured)
        av_data = self._get_alphavantage(symbol)
        if av_data:
            data.alphavantage_articles = av_data.get("articles", [])
            data.alphavantage_sentiment = av_data.get("sentiment", "neutral")
            data.alphavantage_score = av_data.get("score", 0)
            data.alphavantage_volume = len(data.alphavantage_articles)

        # Aggregate sentiment (weighted by volume)
        data.total_mentions = data.stocktwits_volume + data.reddit_volume + data.alphavantage_volume
        data.overall_sentiment = self._aggregate_sentiment(data)
        data.is_trending = data.total_mentions >= 15

        return data

    def _aggregate_sentiment(self, data: SocialData) -> str:
        """Combine sentiment from multiple sources."""
        if data.total_mentions == 0:
            return "neutral"

        # Convert Alpha Vantage score (-1 to 1) to bullish_pct (0 to 100)
        av_bullish_pct = (data.alphavantage_score + 1) * 50  # -1->0%, 0->50%, 1->100%

        # Weight by volume
        total_bullish = (
            data.stocktwits_bullish_pct * data.stocktwits_volume +
            data.reddit_bullish_pct * data.reddit_volume +
            av_bullish_pct * data.alphavantage_volume
        )
        if data.total_mentions > 0:
            avg_bullish = total_bullish / data.total_mentions
        else:
            avg_bullish = 50

        if avg_bullish >= 60:
            return "bullish"
        elif avg_bullish <= 40:
            return "bearish"
        return "neutral"

    def _get_stocktwits(self, symbol: str, limit: int = 30) -> Optional[dict]:
        """
        Fetch recent StockTwits messages for a symbol.

        StockTwits API is free and doesn't require authentication for basic access.
        Rate limit: 200 requests/hour
        """
        try:
            url = f"{self.STOCKTWITS_BASE}/streams/symbol/{symbol}.json"
            params = {"limit": limit}

            response = self.session.get(url, params=params, timeout=10)

            if response.status_code == 404:
                logger.debug("Symbol not found on StockTwits", symbol=symbol)
                return None

            if response.status_code == 429:
                logger.warning("StockTwits rate limit hit")
                return None

            response.raise_for_status()
            data = response.json()

            messages = []
            bullish_count = 0
            bearish_count = 0

            for msg in data.get("messages", []):
                sentiment = None
                if msg.get("entities", {}).get("sentiment"):
                    sentiment = msg["entities"]["sentiment"].get("basic")
                    if sentiment == "Bullish":
                        bullish_count += 1
                    elif sentiment == "Bearish":
                        bearish_count += 1

                messages.append({
                    "id": msg.get("id"),
                    "body": msg.get("body", ""),
                    "sentiment": sentiment,
                    "created_at": msg.get("created_at"),
                    "user": msg.get("user", {}).get("username"),
                    "likes": msg.get("likes", {}).get("total", 0)
                })

            # Calculate overall sentiment
            total_sentiment = bullish_count + bearish_count
            if total_sentiment > 0:
                bullish_pct = (bullish_count / total_sentiment) * 100
                if bullish_pct >= 60:
                    sentiment = "bullish"
                elif bullish_pct <= 40:
                    sentiment = "bearish"
                else:
                    sentiment = "neutral"
            else:
                sentiment = "neutral"
                bullish_pct = 50

            logger.debug(
                "StockTwits data fetched",
                symbol=symbol,
                messages=len(messages),
                bullish=bullish_count,
                bearish=bearish_count,
                sentiment=sentiment
            )

            return {
                "messages": messages,
                "sentiment": sentiment,
                "bullish_pct": bullish_pct,
                "bullish_count": bullish_count,
                "bearish_count": bearish_count
            }

        except requests.exceptions.Timeout:
            logger.warning("StockTwits timeout", symbol=symbol)
            return None
        except requests.exceptions.RequestException as e:
            logger.error("StockTwits error", symbol=symbol, error=str(e))
            return None

    def get_trending(self) -> list[str]:
        """
        Get currently trending symbols on StockTwits.

        Returns:
            List of trending ticker symbols
        """
        try:
            url = f"{self.STOCKTWITS_BASE}/trending/symbols.json"
            response = self.session.get(url, timeout=10)
            response.raise_for_status()

            data = response.json()
            symbols = [s.get("symbol") for s in data.get("symbols", [])]

            logger.debug("Trending symbols fetched", count=len(symbols))
            return symbols

        except Exception as e:
            logger.error("Error fetching trending", error=str(e))
            return []

    def _get_reddit(self, symbol: str, limit: int = 25) -> Optional[dict]:
        """
        Search Reddit for recent posts mentioning a symbol.

        Uses Reddit's public JSON API (no auth required).
        Checks hot posts and filters for symbol mentions.
        Rate limited to avoid 429s.

        Args:
            symbol: Ticker symbol
            limit: Max posts to fetch

        Returns:
            dict with posts, sentiment, bullish_pct
        """
        # Rate limiting
        now = time.time()
        elapsed = now - SocialClient._last_reddit_call
        if elapsed < self.REDDIT_RATE_LIMIT:
            time.sleep(self.REDDIT_RATE_LIMIT - elapsed)
        SocialClient._last_reddit_call = time.time()

        try:
            posts = []
            bullish_keywords = [
                "buy", "calls", "moon", "rocket", "bull", "long",
                "uppies", "tendies", "gain", "yolo", "diamond", "pump"
            ]
            bearish_keywords = [
                "sell", "puts", "crash", "bear", "short",
                "downies", "loss", "dump", "tank", "rip", "bag"
            ]

            bullish_count = 0
            bearish_count = 0

            # Get hot posts and filter for symbol mentions
            # This works better than search which often returns empty
            for subreddit in self.TRADING_SUBREDDITS[:2]:
                try:
                    # Rate limit between subreddit calls
                    time.sleep(0.5)

                    url = f"{self.REDDIT_BASE}/r/{subreddit}/hot.json"
                    params = {"limit": 50}

                    response = self.session.get(url, params=params, timeout=10)

                    if response.status_code == 429:
                        logger.warning("Reddit rate limited", subreddit=subreddit)
                        time.sleep(2)
                        continue

                    if response.status_code != 200:
                        continue

                    data = response.json()
                    children = data.get("data", {}).get("children", [])

                    for child in children:
                        post_data = child.get("data", {})
                        title = post_data.get("title", "")
                        selftext = post_data.get("selftext", "")
                        combined_upper = (title + " " + selftext).upper()
                        combined_lower = combined_upper.lower()

                        # Check if symbol is mentioned
                        # Look for $SYMBOL, SYMBOL (with word boundaries), or common variations
                        symbol_upper = symbol.upper()
                        if (f"${symbol_upper}" in combined_upper or
                            f" {symbol_upper} " in f" {combined_upper} " or
                            combined_upper.startswith(f"{symbol_upper} ") or
                            combined_upper.endswith(f" {symbol_upper}")):

                            # Simple sentiment from keywords
                            post_bullish = sum(1 for kw in bullish_keywords if kw in combined_lower)
                            post_bearish = sum(1 for kw in bearish_keywords if kw in combined_lower)

                            if post_bullish > post_bearish:
                                sentiment = "bullish"
                                bullish_count += 1
                            elif post_bearish > post_bullish:
                                sentiment = "bearish"
                                bearish_count += 1
                            else:
                                sentiment = "neutral"

                            posts.append({
                                "title": title,
                                "subreddit": subreddit,
                                "score": post_data.get("score", 0),
                                "num_comments": post_data.get("num_comments", 0),
                                "created_utc": post_data.get("created_utc"),
                                "url": f"https://reddit.com{post_data.get('permalink', '')}",
                                "sentiment": sentiment
                            })

                            if len(posts) >= limit:
                                break

                except requests.exceptions.RequestException:
                    continue

                if len(posts) >= limit:
                    break

            if not posts:
                return None

            # Calculate overall sentiment
            total_sentiment = bullish_count + bearish_count
            if total_sentiment > 0:
                bullish_pct = (bullish_count / total_sentiment) * 100
                if bullish_pct >= 60:
                    sentiment = "bullish"
                elif bullish_pct <= 40:
                    sentiment = "bearish"
                else:
                    sentiment = "neutral"
            else:
                sentiment = "neutral"
                bullish_pct = 50

            logger.debug(
                "Reddit data fetched",
                symbol=symbol,
                posts=len(posts),
                bullish=bullish_count,
                bearish=bearish_count,
                sentiment=sentiment
            )

            return {
                "posts": posts,
                "sentiment": sentiment,
                "bullish_pct": bullish_pct,
                "bullish_count": bullish_count,
                "bearish_count": bearish_count
            }

        except Exception as e:
            logger.error("Reddit error", symbol=symbol, error=str(e))
            return None

    def _get_alphavantage(self, symbol: str, limit: int = 10) -> Optional[dict]:
        """
        Fetch news sentiment from Alpha Vantage.

        Alpha Vantage provides AI-scored news sentiment.
        Free tier: 25 requests/day
        Requires ALPHAVANTAGE_API_KEY in .env

        Args:
            symbol: Ticker symbol
            limit: Max articles to fetch

        Returns:
            dict with articles, sentiment, score (-1 to 1)
        """
        if not self.alphavantage_key:
            return None

        try:
            params = {
                "function": "NEWS_SENTIMENT",
                "tickers": symbol,
                "limit": limit,
                "apikey": self.alphavantage_key
            }

            response = self.session.get(
                self.ALPHAVANTAGE_BASE,
                params=params,
                timeout=15
            )

            if response.status_code != 200:
                logger.warning("Alpha Vantage error", status=response.status_code)
                return None

            data = response.json()

            # Check for API limit message
            if "Note" in data or "Information" in data:
                logger.warning("Alpha Vantage rate limit or invalid key")
                return None

            feed = data.get("feed", [])
            if not feed:
                return None

            articles = []
            total_score = 0
            scored_count = 0

            for item in feed:
                # Find ticker-specific sentiment
                ticker_sentiment = None
                for ts in item.get("ticker_sentiment", []):
                    if ts.get("ticker") == symbol:
                        ticker_sentiment = ts
                        break

                if ticker_sentiment:
                    score = float(ticker_sentiment.get("ticker_sentiment_score", 0))
                    label = ticker_sentiment.get("ticker_sentiment_label", "Neutral")
                    relevance = float(ticker_sentiment.get("relevance_score", 0))

                    # Only count articles with decent relevance
                    if relevance >= 0.1:
                        total_score += score
                        scored_count += 1

                    articles.append({
                        "title": item.get("title", ""),
                        "source": item.get("source", ""),
                        "url": item.get("url", ""),
                        "time_published": item.get("time_published", ""),
                        "sentiment_score": score,
                        "sentiment_label": label,
                        "relevance": relevance
                    })

            # Calculate average sentiment
            if scored_count > 0:
                avg_score = total_score / scored_count
                if avg_score >= 0.15:
                    sentiment = "bullish"
                elif avg_score <= -0.15:
                    sentiment = "bearish"
                else:
                    sentiment = "neutral"
            else:
                avg_score = 0
                sentiment = "neutral"

            logger.debug(
                "Alpha Vantage data fetched",
                symbol=symbol,
                articles=len(articles),
                avg_score=avg_score,
                sentiment=sentiment
            )

            return {
                "articles": articles,
                "sentiment": sentiment,
                "score": avg_score
            }

        except Exception as e:
            logger.error("Alpha Vantage error", symbol=symbol, error=str(e))
            return None


# Singleton instance
_social_client: Optional[SocialClient] = None


def get_social_client() -> SocialClient:
    """Get the global social client instance."""
    global _social_client
    if _social_client is None:
        _social_client = SocialClient()
    return _social_client
