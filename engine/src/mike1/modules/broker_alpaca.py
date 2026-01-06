"""
Alpaca Broker Integration for MIKE-1

The primary broker - official API, designed for algo trading.

Docs: https://docs.alpaca.markets/
SDK: https://github.com/alpacahq/alpaca-py
"""

from datetime import datetime, date
from typing import Optional
import os
import structlog

from .broker import Broker, OptionQuote, OptionPosition, OrderResult

logger = structlog.get_logger()


class AlpacaBroker(Broker):
    """
    Alpaca broker implementation using alpaca-py SDK.

    Supports:
    - Stocks
    - Options
    - Paper trading
    - Live trading

    Requires:
    - ALPACA_API_KEY
    - ALPACA_SECRET_KEY
    - ALPACA_PAPER (true/false)
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        secret_key: Optional[str] = None,
        paper: bool = True
    ):
        self.api_key = api_key or os.environ.get("ALPACA_API_KEY")
        self.secret_key = secret_key or os.environ.get("ALPACA_SECRET_KEY")
        self.paper = paper if paper is not None else os.environ.get("ALPACA_PAPER", "true").lower() == "true"

        self.connected = False
        self._trading_client = None
        self._data_client = None
        self._option_data_client = None

    def connect(self) -> bool:
        """
        Connect to Alpaca API.

        Uses API key authentication - simple and reliable.
        """
        try:
            from alpaca.trading.client import TradingClient
            from alpaca.data.historical import StockHistoricalDataClient
            from alpaca.data.historical.option import OptionHistoricalDataClient

            if not self.api_key or not self.secret_key:
                logger.error("Alpaca credentials not provided")
                logger.info("Set ALPACA_API_KEY and ALPACA_SECRET_KEY in .env")
                return False

            # Initialize trading client
            self._trading_client = TradingClient(
                api_key=self.api_key,
                secret_key=self.secret_key,
                paper=self.paper
            )

            # Initialize data clients
            self._data_client = StockHistoricalDataClient(
                api_key=self.api_key,
                secret_key=self.secret_key
            )

            self._option_data_client = OptionHistoricalDataClient(
                api_key=self.api_key,
                secret_key=self.secret_key
            )

            # Test connection by getting account
            account = self._trading_client.get_account()

            if account:
                self.connected = True
                logger.info(
                    "Connected to Alpaca",
                    paper=self.paper,
                    account_status=account.status,
                    buying_power=float(account.buying_power)
                )
                return True
            else:
                logger.error("Failed to get Alpaca account")
                return False

        except ImportError:
            logger.error("alpaca-py not installed. Run: pip install alpaca-py")
            return False
        except Exception as e:
            logger.error("Alpaca connection error", error=str(e))
            return False

    def disconnect(self) -> None:
        """Disconnect from Alpaca."""
        self._trading_client = None
        self._data_client = None
        self._option_data_client = None
        self.connected = False
        logger.info("Disconnected from Alpaca")

    def get_account_info(self) -> dict:
        """Get Alpaca account information."""
        if not self.connected or not self._trading_client:
            return {}

        try:
            account = self._trading_client.get_account()

            return {
                "buying_power": float(account.buying_power),
                "cash": float(account.cash),
                "portfolio_value": float(account.portfolio_value),
                "equity": float(account.equity),
                "last_equity": float(account.last_equity),
                "status": account.status,
                "trading_blocked": account.trading_blocked,
                "pattern_day_trader": account.pattern_day_trader,
                "daytrading_buying_power": float(account.daytrading_buying_power) if account.daytrading_buying_power else 0,
            }
        except Exception as e:
            logger.error("Error getting Alpaca account info", error=str(e))
            return {}

    def get_option_positions(self) -> list[OptionPosition]:
        """Get all current option positions from Alpaca."""
        if not self.connected or not self._trading_client:
            return []

        try:
            positions = self._trading_client.get_all_positions()
            result = []

            for pos in positions:
                # Filter for options only (asset_class == 'us_option')
                if pos.asset_class == "us_option":
                    # Parse option symbol to extract details
                    parsed = self._parse_option_symbol(pos.symbol)

                    if parsed:
                        result.append(OptionPosition(
                            id=str(pos.asset_id),
                            symbol=parsed["underlying"],
                            option_type=parsed["option_type"],
                            strike=parsed["strike"],
                            expiration=parsed["expiration"],
                            quantity=float(pos.qty),
                            average_cost=float(pos.avg_entry_price),
                            current_price=float(pos.current_price),
                        ))

            return result

        except Exception as e:
            logger.error("Error getting Alpaca option positions", error=str(e))
            return []

    def _parse_option_symbol(self, symbol: str) -> Optional[dict]:
        """
        Parse OCC option symbol format.

        Format: UNDERLYING + YYMMDD + C/P + Strike (padded)
        Example: AAPL240119C00185000 = AAPL Jan 19 2024 $185 Call
        """
        try:
            # Find where date starts (look for 6 consecutive digits)
            for i in range(len(symbol) - 15, 0, -1):
                potential_date = symbol[i:i+6]
                if potential_date.isdigit():
                    underlying = symbol[:i]
                    date_str = potential_date
                    option_type = "call" if symbol[i+6] == "C" else "put"
                    strike = float(symbol[i+7:]) / 1000

                    # Parse date
                    year = 2000 + int(date_str[:2])
                    month = int(date_str[2:4])
                    day = int(date_str[4:6])
                    expiration = f"{year}-{month:02d}-{day:02d}"

                    return {
                        "underlying": underlying,
                        "expiration": expiration,
                        "option_type": option_type,
                        "strike": strike
                    }

            return None
        except Exception:
            return None

    def _build_option_symbol(
        self,
        symbol: str,
        expiration: str,
        option_type: str,
        strike: float
    ) -> str:
        """
        Build OCC option symbol from components.

        Args:
            symbol: Underlying symbol (e.g., "AAPL")
            expiration: Date string "YYYY-MM-DD"
            option_type: "call" or "put"
            strike: Strike price

        Returns:
            OCC symbol (e.g., "AAPL240119C00185000")
        """
        # Parse expiration
        year = expiration[2:4]
        month = expiration[5:7]
        day = expiration[8:10]

        # Option type
        opt_char = "C" if option_type.lower() == "call" else "P"

        # Strike (8 digits, 3 implied decimals)
        strike_str = f"{int(strike * 1000):08d}"

        return f"{symbol}{year}{month}{day}{opt_char}{strike_str}"

    def get_option_quote(
        self,
        symbol: str,
        strike: float,
        expiration: str,
        option_type: str
    ) -> Optional[OptionQuote]:
        """Get quote for a specific option contract."""
        if not self.connected or not self._option_data_client:
            return None

        try:
            from alpaca.data.requests import OptionLatestQuoteRequest, OptionSnapshotRequest

            # Build OCC symbol
            occ_symbol = self._build_option_symbol(symbol, expiration, option_type, strike)

            # Get latest quote
            request = OptionLatestQuoteRequest(symbol_or_symbols=occ_symbol)
            quotes = self._option_data_client.get_option_latest_quote(request)

            if occ_symbol not in quotes:
                return None

            quote = quotes[occ_symbol]

            # Get underlying price
            stock_price = self.get_stock_price(symbol)

            # Try to get greeks, volume, and open interest from snapshot
            delta = gamma = theta = vega = iv = 0.0
            volume = 0
            open_interest = 0
            last_price = 0.0
            try:
                snap_request = OptionSnapshotRequest(symbol_or_symbols=occ_symbol)
                snapshots = self._option_data_client.get_option_snapshot(snap_request)
                if occ_symbol in snapshots:
                    snap = snapshots[occ_symbol]
                    if snap.greeks:
                        delta = snap.greeks.delta or 0
                        gamma = snap.greeks.gamma or 0
                        theta = snap.greeks.theta or 0
                        vega = snap.greeks.vega or 0
                    if snap.implied_volatility:
                        iv = snap.implied_volatility
                    # Get volume from daily bar if available
                    if hasattr(snap, 'daily_bar') and snap.daily_bar:
                        volume = int(snap.daily_bar.volume or 0)
                    # Get last trade price
                    if hasattr(snap, 'latest_trade') and snap.latest_trade:
                        last_price = float(snap.latest_trade.price or 0)
                    # Get open interest if available
                    if hasattr(snap, 'open_interest'):
                        open_interest = int(snap.open_interest or 0)
            except:
                pass

            # If no open interest from snapshot, try option contracts API
            if open_interest == 0:
                try:
                    from alpaca.trading.requests import GetOptionContractsRequest
                    contracts_request = GetOptionContractsRequest(
                        underlying_symbols=[symbol],
                        expiration_date=expiration,
                        strike_price_gte=str(strike - 0.01),
                        strike_price_lte=str(strike + 0.01),
                        type=option_type
                    )
                    contracts = self._trading_client.get_option_contracts(contracts_request)
                    if contracts and hasattr(contracts, 'option_contracts'):
                        for contract in contracts.option_contracts:
                            if hasattr(contract, 'open_interest'):
                                open_interest = int(contract.open_interest or 0)
                                break
                except Exception as oi_err:
                    logger.debug("Could not fetch open interest", error=str(oi_err))

            return OptionQuote(
                symbol=symbol,
                strike=strike,
                expiration=expiration,
                option_type=option_type,
                bid=float(quote.bid_price) if quote.bid_price else 0,
                ask=float(quote.ask_price) if quote.ask_price else 0,
                mark=(float(quote.bid_price or 0) + float(quote.ask_price or 0)) / 2,
                last=last_price,
                volume=volume,
                open_interest=open_interest,
                implied_volatility=iv,
                delta=delta,
                gamma=gamma,
                theta=theta,
                vega=vega,
                underlying_price=stock_price,
            )

        except Exception as e:
            logger.error("Error getting Alpaca option quote", symbol=symbol, error=str(e))
            return None

    def get_option_chain(
        self,
        symbol: str,
        expiration: str,
        option_type: str
    ) -> list[OptionQuote]:
        """Get option chain for a symbol and expiration."""
        if not self.connected or not self._option_data_client:
            return []

        try:
            from alpaca.data.requests import OptionChainRequest

            # Get option chain
            request = OptionChainRequest(
                underlying_symbol=symbol,
                expiration_date=expiration,
                type=option_type
            )

            chain = self._option_data_client.get_option_chain(request)
            stock_price = self.get_stock_price(symbol)

            result = []
            for occ_symbol, data in chain.items():
                parsed = self._parse_option_symbol(occ_symbol)
                if parsed:
                    quote = data.latest_quote
                    snap = data

                    delta = gamma = theta = vega = iv = 0.0
                    if hasattr(snap, 'greeks') and snap.greeks:
                        delta = snap.greeks.delta or 0
                        gamma = snap.greeks.gamma or 0
                        theta = snap.greeks.theta or 0
                        vega = snap.greeks.vega or 0
                    if hasattr(snap, 'implied_volatility'):
                        iv = snap.implied_volatility or 0

                    result.append(OptionQuote(
                        symbol=symbol,
                        strike=parsed["strike"],
                        expiration=expiration,
                        option_type=option_type,
                        bid=float(quote.bid_price) if quote and quote.bid_price else 0,
                        ask=float(quote.ask_price) if quote and quote.ask_price else 0,
                        mark=(float(quote.bid_price or 0) + float(quote.ask_price or 0)) / 2 if quote else 0,
                        last=0,
                        volume=0,
                        open_interest=0,
                        implied_volatility=iv,
                        delta=delta,
                        gamma=gamma,
                        theta=theta,
                        vega=vega,
                        underlying_price=stock_price,
                    ))

            return result

        except Exception as e:
            logger.error("Error getting Alpaca option chain", symbol=symbol, error=str(e))
            return []

    def buy_option(
        self,
        symbol: str,
        strike: float,
        expiration: str,
        option_type: str,
        quantity: int,
        price: Optional[float] = None
    ) -> OrderResult:
        """Buy an option contract on Alpaca."""
        if not self.connected or not self._trading_client:
            return OrderResult(success=False, message="Not connected to broker")

        try:
            from alpaca.trading.requests import MarketOrderRequest, LimitOrderRequest
            from alpaca.trading.enums import OrderSide, TimeInForce

            # Build OCC symbol
            occ_symbol = self._build_option_symbol(symbol, expiration, option_type, strike)

            if price is not None:
                # Limit order
                order_request = LimitOrderRequest(
                    symbol=occ_symbol,
                    qty=quantity,
                    side=OrderSide.BUY,
                    time_in_force=TimeInForce.DAY,
                    limit_price=price
                )
            else:
                # Market order
                order_request = MarketOrderRequest(
                    symbol=occ_symbol,
                    qty=quantity,
                    side=OrderSide.BUY,
                    time_in_force=TimeInForce.DAY
                )

            order = self._trading_client.submit_order(order_request)

            if order:
                logger.info(
                    "Alpaca buy order placed",
                    symbol=symbol,
                    strike=strike,
                    quantity=quantity,
                    order_id=order.id
                )
                return OrderResult(
                    success=True,
                    order_id=str(order.id),
                    filled_quantity=float(order.filled_qty) if order.filled_qty else 0,
                    filled_price=float(order.filled_avg_price) if order.filled_avg_price else 0,
                    message=f"Order {order.status}"
                )
            else:
                return OrderResult(success=False, message="Order submission failed")

        except Exception as e:
            logger.error("Error placing Alpaca buy order", error=str(e))
            return OrderResult(success=False, message=str(e))

    def sell_option(
        self,
        symbol: str,
        strike: float,
        expiration: str,
        option_type: str,
        quantity: int,
        price: Optional[float] = None
    ) -> OrderResult:
        """Sell an option contract on Alpaca."""
        if not self.connected or not self._trading_client:
            return OrderResult(success=False, message="Not connected to broker")

        try:
            from alpaca.trading.requests import MarketOrderRequest, LimitOrderRequest
            from alpaca.trading.enums import OrderSide, TimeInForce

            # Build OCC symbol
            occ_symbol = self._build_option_symbol(symbol, expiration, option_type, strike)

            if price is not None:
                order_request = LimitOrderRequest(
                    symbol=occ_symbol,
                    qty=quantity,
                    side=OrderSide.SELL,
                    time_in_force=TimeInForce.DAY,
                    limit_price=price
                )
            else:
                order_request = MarketOrderRequest(
                    symbol=occ_symbol,
                    qty=quantity,
                    side=OrderSide.SELL,
                    time_in_force=TimeInForce.DAY
                )

            order = self._trading_client.submit_order(order_request)

            if order:
                logger.info(
                    "Alpaca sell order placed",
                    symbol=symbol,
                    strike=strike,
                    quantity=quantity,
                    order_id=order.id
                )
                return OrderResult(
                    success=True,
                    order_id=str(order.id),
                    filled_quantity=float(order.filled_qty) if order.filled_qty else 0,
                    filled_price=float(order.filled_avg_price) if order.filled_avg_price else 0,
                    message=f"Order {order.status}"
                )
            else:
                return OrderResult(success=False, message="Order submission failed")

        except Exception as e:
            logger.error("Error placing Alpaca sell order", error=str(e))
            return OrderResult(success=False, message=str(e))

    def get_stock_price(self, symbol: str) -> float:
        """Get current stock price from Alpaca."""
        if not self.connected or not self._data_client:
            return 0

        try:
            from alpaca.data.requests import StockLatestQuoteRequest

            request = StockLatestQuoteRequest(symbol_or_symbols=symbol)
            quotes = self._data_client.get_stock_latest_quote(request)

            if symbol in quotes:
                quote = quotes[symbol]
                # Use mid price
                return (float(quote.bid_price) + float(quote.ask_price)) / 2

            return 0
        except Exception as e:
            logger.error("Error getting Alpaca stock price", symbol=symbol, error=str(e))
            return 0

    def get_atr(self, symbol: str, period: int = 14) -> float:
        """
        Calculate ATR (Average True Range) for a symbol.

        ATR = Average of True Range over N periods
        True Range = max(high-low, abs(high-prev_close), abs(low-prev_close))

        Args:
            symbol: Stock symbol (underlying)
            period: ATR period (default 14)

        Returns:
            ATR value in dollars, or 0 if unavailable
        """
        if not self.connected or not self._data_client:
            return 0

        try:
            from alpaca.data.requests import StockBarsRequest
            from alpaca.data.timeframe import TimeFrame
            from datetime import timedelta

            # Get enough bars for ATR calculation (need period + 1 for prev close)
            end = datetime.now()
            start = end - timedelta(days=period * 2)  # Buffer for weekends/holidays

            request = StockBarsRequest(
                symbol_or_symbols=symbol,
                timeframe=TimeFrame.Day,
                start=start,
                end=end
            )

            bars = self._data_client.get_stock_bars(request)

            if symbol not in bars or len(bars[symbol]) < period + 1:
                logger.warning("Insufficient bars for ATR", symbol=symbol)
                return 0

            bar_list = list(bars[symbol])

            # Calculate True Range for each bar
            true_ranges = []
            for i in range(1, len(bar_list)):
                high = float(bar_list[i].high)
                low = float(bar_list[i].low)
                prev_close = float(bar_list[i-1].close)

                tr = max(
                    high - low,
                    abs(high - prev_close),
                    abs(low - prev_close)
                )
                true_ranges.append(tr)

            # Take last N periods
            if len(true_ranges) < period:
                return 0

            recent_tr = true_ranges[-period:]
            atr = sum(recent_tr) / len(recent_tr)

            logger.debug("ATR calculated", symbol=symbol, atr=f"${atr:.2f}", period=period)
            return atr

        except Exception as e:
            logger.error("Error calculating ATR", symbol=symbol, error=str(e))
            return 0

    def get_order_status(self, order_id: str) -> Optional[dict]:
        """Get status of an order."""
        if not self.connected or not self._trading_client:
            return None

        try:
            order = self._trading_client.get_order_by_id(order_id)

            return {
                "id": str(order.id),
                "status": order.status,
                "filled_qty": float(order.filled_qty) if order.filled_qty else 0,
                "filled_avg_price": float(order.filled_avg_price) if order.filled_avg_price else 0,
                "submitted_at": order.submitted_at.isoformat() if order.submitted_at else None,
                "filled_at": order.filled_at.isoformat() if order.filled_at else None,
            }
        except Exception as e:
            logger.error("Error getting order status", order_id=order_id, error=str(e))
            return None

    def cancel_order(self, order_id: str) -> bool:
        """Cancel an open order."""
        if not self.connected or not self._trading_client:
            return False

        try:
            self._trading_client.cancel_order_by_id(order_id)
            logger.info("Order cancelled", order_id=order_id)
            return True
        except Exception as e:
            logger.error("Error cancelling order", order_id=order_id, error=str(e))
            return False

    # =========================================================================
    # JUDGE DATA METHODS - Technical indicators for trade scoring
    # =========================================================================

    def get_volume_data(self, symbol: str) -> Optional[dict]:
        """
        Get current and average volume for a symbol.

        Returns:
            dict with current_volume, avg_volume, or None on error
        """
        if not self.connected or not self._data_client:
            return None

        try:
            from alpaca.data.requests import StockBarsRequest
            from alpaca.data.timeframe import TimeFrame
            from datetime import timedelta

            end = datetime.now()
            start = end - timedelta(days=30)  # 30 days for avg

            request = StockBarsRequest(
                symbol_or_symbols=symbol,
                timeframe=TimeFrame.Day,
                start=start,
                end=end
            )

            bars = self._data_client.get_stock_bars(request)

            # Access via .data dict
            if symbol not in bars.data or len(bars.data[symbol]) < 2:
                return None

            bar_list = bars.data[symbol]

            # Current volume = today's (or most recent) volume
            current_volume = int(bar_list[-1].volume)

            # Average volume = 20-day average (excluding today)
            if len(bar_list) > 20:
                recent_bars = bar_list[-21:-1]  # Last 20 days before today
            else:
                recent_bars = bar_list[:-1]  # All except today

            if not recent_bars:
                return {"current_volume": current_volume, "avg_volume": current_volume}

            avg_volume = int(sum(b.volume for b in recent_bars) / len(recent_bars))

            return {
                "current_volume": current_volume,
                "avg_volume": avg_volume
            }

        except Exception as e:
            logger.error("Error getting volume data", symbol=symbol, error=str(e))
            return None

    def get_vwap(self, symbol: str) -> Optional[dict]:
        """
        Get VWAP (Volume Weighted Average Price) for today.

        Returns:
            dict with vwap, or None on error
        """
        if not self.connected or not self._data_client:
            return None

        try:
            from alpaca.data.requests import StockBarsRequest
            from alpaca.data.timeframe import TimeFrame
            from datetime import timedelta

            # Get intraday bars for today
            end = datetime.now()
            start = end.replace(hour=9, minute=30, second=0, microsecond=0)

            # If before market open, use yesterday
            if end < start:
                start = start - timedelta(days=1)

            request = StockBarsRequest(
                symbol_or_symbols=symbol,
                timeframe=TimeFrame.Minute,
                start=start,
                end=end
            )

            bars = self._data_client.get_stock_bars(request)

            if symbol not in bars or len(bars[symbol]) == 0:
                return None

            bar_list = list(bars[symbol])

            # Calculate VWAP = sum(price * volume) / sum(volume)
            total_pv = 0
            total_volume = 0

            for bar in bar_list:
                typical_price = (float(bar.high) + float(bar.low) + float(bar.close)) / 3
                total_pv += typical_price * float(bar.volume)
                total_volume += float(bar.volume)

            if total_volume == 0:
                return None

            vwap = total_pv / total_volume

            return {"vwap": vwap}

        except Exception as e:
            logger.error("Error getting VWAP", symbol=symbol, error=str(e))
            return None

    def get_rsi(self, symbol: str, period: int = 14) -> float:
        """
        Calculate RSI (Relative Strength Index) for a symbol.

        RSI = 100 - (100 / (1 + RS))
        RS = Average Gain / Average Loss over N periods

        Args:
            symbol: Stock symbol
            period: RSI period (default 14)

        Returns:
            RSI value (0-100), or 50 on error
        """
        if not self.connected or not self._data_client:
            return 50  # Neutral default

        try:
            from alpaca.data.requests import StockBarsRequest
            from alpaca.data.timeframe import TimeFrame
            from datetime import timedelta

            # Get enough bars for RSI calculation
            end = datetime.now()
            start = end - timedelta(days=period * 2)  # Buffer for weekends

            request = StockBarsRequest(
                symbol_or_symbols=symbol,
                timeframe=TimeFrame.Day,
                start=start,
                end=end
            )

            bars = self._data_client.get_stock_bars(request)

            if symbol not in bars or len(bars[symbol]) < period + 1:
                return 50

            bar_list = list(bars[symbol])

            # Calculate price changes
            gains = []
            losses = []

            for i in range(1, len(bar_list)):
                change = float(bar_list[i].close) - float(bar_list[i-1].close)
                if change >= 0:
                    gains.append(change)
                    losses.append(0)
                else:
                    gains.append(0)
                    losses.append(abs(change))

            # Take last N periods
            if len(gains) < period:
                return 50

            recent_gains = gains[-period:]
            recent_losses = losses[-period:]

            avg_gain = sum(recent_gains) / period
            avg_loss = sum(recent_losses) / period

            if avg_loss == 0:
                return 100  # All gains

            rs = avg_gain / avg_loss
            rsi = 100 - (100 / (1 + rs))

            logger.debug("RSI calculated", symbol=symbol, rsi=f"{rsi:.1f}", period=period)
            return rsi

        except Exception as e:
            logger.error("Error calculating RSI", symbol=symbol, error=str(e))
            return 50

    def get_news(self, symbol: str, limit: int = 5) -> list[dict]:
        """
        Get recent news headlines for a symbol.

        Args:
            symbol: Stock symbol
            limit: Max headlines to return

        Returns:
            List of dicts with headline, summary, url, timestamp
        """
        if not self.connected:
            return []

        try:
            from alpaca.data.historical.news import NewsClient
            from alpaca.data.requests import NewsRequest

            news_client = NewsClient(
                api_key=self.api_key,
                secret_key=self.secret_key
            )

            request = NewsRequest(
                symbols=symbol,
                limit=limit
            )

            news = news_client.get_news(request)

            results = []
            # Access nested data structure - news.data is a dict with 'news' key containing list
            news_list = []
            if hasattr(news, 'data') and isinstance(news.data, dict):
                news_list = news.data.get('news', [])

            for article in news_list:
                # Articles can be News objects or dicts depending on SDK version
                if hasattr(article, 'headline'):
                    # It's a News object
                    results.append({
                        "headline": article.headline or "",
                        "summary": article.summary if hasattr(article, 'summary') else "",
                        "url": article.url if hasattr(article, 'url') else "",
                        "timestamp": article.created_at.isoformat() if article.created_at else "",
                        "source": article.source if hasattr(article, 'source') else ""
                    })
                else:
                    # It's a dict
                    results.append({
                        "headline": article.get("headline", ""),
                        "summary": article.get("summary", ""),
                        "url": article.get("url", ""),
                        "timestamp": article.get("created_at", ""),
                        "source": article.get("source", "")
                    })

            return results

        except Exception as e:
            logger.error("Error getting news", symbol=symbol, error=str(e))
            return []
