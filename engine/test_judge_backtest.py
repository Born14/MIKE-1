#!/usr/bin/env python
"""
Judge Backtest - Test scoring accuracy against historical outcomes.

Two modes:
1. --from-db: Analyze actual trades from database (best - real grades, real outcomes)
2. --live: Run Judge on current market and simulate outcomes (limited - market must be open)

The goal is to validate that A-TIER > B-TIER > NO_TRADE in actual P&L.

Usage:
    python test_judge_backtest.py --from-db   # Analyze past trades
    python test_judge_backtest.py --live      # Live market test
    python test_judge_backtest.py --ticker NVDA --live
"""

import argparse
import sys
import os
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import Optional

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from dotenv import load_dotenv
load_dotenv()


@dataclass
class BacktestTrade:
    """A simulated trade for backtesting."""
    ticker: str
    direction: str
    grade: str
    score: float
    entry_time: datetime
    entry_price: float
    exit_time: Optional[datetime] = None
    exit_price: Optional[float] = None
    exit_reason: Optional[str] = None
    pnl_pct: float = 0.0
    high_water_mark: float = 0.0

    @property
    def is_winner(self) -> bool:
        return self.pnl_pct > 0


@dataclass
class BacktestResults:
    """Aggregated backtest results."""
    total_trades: int = 0

    # By grade
    a_tier_trades: list = field(default_factory=list)
    b_tier_trades: list = field(default_factory=list)
    no_trade_trades: list = field(default_factory=list)

    def add_trade(self, trade: BacktestTrade):
        self.total_trades += 1
        if trade.grade == "A":
            self.a_tier_trades.append(trade)
        elif trade.grade == "B":
            self.b_tier_trades.append(trade)
        else:
            self.no_trade_trades.append(trade)

    def _grade_stats(self, trades: list) -> dict:
        if not trades:
            return {"count": 0, "win_rate": 0, "avg_pnl": 0, "total_pnl": 0}

        winners = [t for t in trades if t.is_winner]
        pnls = [t.pnl_pct for t in trades]

        return {
            "count": len(trades),
            "win_rate": len(winners) / len(trades) * 100,
            "avg_pnl": sum(pnls) / len(pnls),
            "total_pnl": sum(pnls),
            "best": max(pnls) if pnls else 0,
            "worst": min(pnls) if pnls else 0,
        }

    def summary(self) -> dict:
        return {
            "total_trades": self.total_trades,
            "A_TIER": self._grade_stats(self.a_tier_trades),
            "B_TIER": self._grade_stats(self.b_tier_trades),
            "NO_TRADE": self._grade_stats(self.no_trade_trades),
        }

    def print_report(self):
        """Print formatted backtest report."""
        s = self.summary()

        print()
        print("=" * 70)
        print("JUDGE BACKTEST RESULTS")
        print("=" * 70)
        print()
        print(f"Total Setups Evaluated: {s['total_trades']}")
        print()

        print(f"{'Grade':<12} {'Count':>6} {'Win %':>8} {'Avg P&L':>10} {'Total P&L':>12} {'Best':>8} {'Worst':>8}")
        print("-" * 70)

        for grade in ["A_TIER", "B_TIER", "NO_TRADE"]:
            g = s[grade]
            if g["count"] > 0:
                print(f"{grade:<12} {g['count']:>6} {g['win_rate']:>7.1f}% {g['avg_pnl']:>9.1f}% {g['total_pnl']:>11.1f}% {g['best']:>7.1f}% {g['worst']:>7.1f}%")
            else:
                print(f"{grade:<12} {g['count']:>6} {'--':>8} {'--':>10} {'--':>12} {'--':>8} {'--':>8}")

        print()

        # Validation: Does A > B > NO_TRADE?
        a_avg = s["A_TIER"]["avg_pnl"] if s["A_TIER"]["count"] > 0 else float('-inf')
        b_avg = s["B_TIER"]["avg_pnl"] if s["B_TIER"]["count"] > 0 else float('-inf')
        no_avg = s["NO_TRADE"]["avg_pnl"] if s["NO_TRADE"]["count"] > 0 else float('-inf')

        print("VALIDATION:")
        if a_avg > b_avg > no_avg:
            print("  [PASS] A-TIER > B-TIER > NO_TRADE (as expected)")
        elif a_avg > b_avg:
            print("  [PARTIAL] A-TIER > B-TIER (good), but NO_TRADE not worst")
        elif a_avg > no_avg:
            print("  [PARTIAL] A-TIER > NO_TRADE, but B-TIER ordering off")
        else:
            print("  [FAIL] Grade ordering does not match P&L performance")
            print(f"         A={a_avg:.1f}% B={b_avg:.1f}% NO={no_avg:.1f}%")

        # Win rate validation
        a_wr = s["A_TIER"]["win_rate"] if s["A_TIER"]["count"] > 0 else 0
        b_wr = s["B_TIER"]["win_rate"] if s["B_TIER"]["count"] > 0 else 0
        no_wr = s["NO_TRADE"]["win_rate"] if s["NO_TRADE"]["count"] > 0 else 0

        if a_wr > b_wr > no_wr:
            print("  [PASS] Win rate: A-TIER > B-TIER > NO_TRADE")
        else:
            print(f"  [INFO] Win rates: A={a_wr:.0f}% B={b_wr:.0f}% NO={no_wr:.0f}%")

        print()


class JudgeBacktester:
    """
    Backtest Judge scoring against historical price data.

    Strategy:
    - Entry: At market data snapshot time
    - Exit: First of:
        - +25% (winner)
        - -50% (hard stop)
        - End of day (time exit)
    """

    # Simple exit rules for backtesting
    PROFIT_TARGET = 0.25  # +25%
    HARD_STOP = -0.50     # -50%

    def __init__(self, broker, judge, lookback_days: int = 5):
        self.broker = broker
        self.judge = judge
        self.lookback_days = lookback_days
        self.results = BacktestResults()

    def _get_historical_bars(self, symbol: str, days: int):
        """Fetch historical daily bars from Alpaca."""
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame

        end = datetime.now()
        start = end - timedelta(days=days + 15)  # Buffer for weekends/holidays

        request = StockBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=TimeFrame.Day,
            start=start,
            end=end
        )

        try:
            bars_data = self.broker._data_client.get_stock_bars(request)

            # BarSet uses [] access, not 'in' operator
            try:
                bar_list = list(bars_data[symbol])
                return bar_list
            except (KeyError, TypeError):
                return []
        except Exception as e:
            print(f"[bars error: {e}]", end=" ")
            return []

    def run(self, tickers: list[str], directions: list[str] = None):
        """
        Run backtest on specified tickers.

        For each ticker, we:
        1. Get historical 5-min bars for the lookback period
        2. At each day's open, score the setup with Judge
        3. Simulate intraday price movement to determine outcome
        """
        if directions is None:
            directions = ["call", "put"]

        print(f"Running backtest on {len(tickers)} tickers...")
        print(f"Lookback: {self.lookback_days} days")
        print(f"Directions: {directions}")
        print()

        for ticker in tickers:
            for direction in directions:
                self._backtest_ticker(ticker, direction)

        return self.results

    def _backtest_ticker(self, ticker: str, direction: str):
        """Backtest a single ticker/direction combo."""
        print(f"  Testing {ticker} {direction}...", end=" ")

        try:
            # Get historical daily bars
            bars = self._get_historical_bars(ticker, self.lookback_days + 5)

            if not bars or len(bars) < 3:
                print(f"insufficient data (got {len(bars) if bars else 0} bars)")
                return

            # For each trading day, simulate entry at open
            trades_found = 0
            for i in range(2, min(len(bars), self.lookback_days + 2)):
                bar = bars[i]
                prev_bar = bars[i-1]

                # Skip weekends/holidays (no bar)
                if bar is None:
                    continue

                # Create a "snapshot" market state for that day
                # We'll use the day's open as entry, high/low for outcome
                entry_price = bar.open
                day_high = bar.high
                day_low = bar.low
                day_close = bar.close

                # Score with Judge (using current market state - simplified)
                # In reality, we'd need historical VWAP, RSI, etc.
                verdict = self.judge.grade(
                    symbol=ticker,
                    direction=direction,
                    strike=None,  # ATM
                    expiration=None  # Default
                )

                if verdict is None:
                    continue

                # Simulate intraday outcome based on direction
                trade = self._simulate_trade(
                    ticker=ticker,
                    direction=direction,
                    grade=verdict.grade.value[0],  # "A", "B", or "N"
                    score=verdict.score,
                    entry_time=bar.timestamp if hasattr(bar, 'timestamp') else datetime.now(),
                    entry_price=entry_price,
                    day_high=day_high,
                    day_low=day_low,
                    day_close=day_close
                )

                self.results.add_trade(trade)
                trades_found += 1

            print(f"{trades_found} setups")

        except Exception as e:
            print(f"error: {e}")

    def _simulate_trade(
        self,
        ticker: str,
        direction: str,
        grade: str,
        score: float,
        entry_time: datetime,
        entry_price: float,
        day_high: float,
        day_low: float,
        day_close: float
    ) -> BacktestTrade:
        """
        Simulate a trade outcome based on intraday price action.

        For CALL: profit if price goes up
        For PUT: profit if price goes down

        Simplified: Use day's high/low to determine if targets hit
        """
        trade = BacktestTrade(
            ticker=ticker,
            direction=direction,
            grade=grade,
            score=score,
            entry_time=entry_time,
            entry_price=entry_price,
        )

        if direction == "call":
            # Call profits from up moves
            max_gain = (day_high - entry_price) / entry_price
            max_loss = (day_low - entry_price) / entry_price
            close_pnl = (day_close - entry_price) / entry_price

            # Option leverage approximation (delta ~0.35 = 2.5-3x leverage)
            leverage = 2.5
            max_gain *= leverage
            max_loss *= leverage
            close_pnl *= leverage

        else:  # put
            # Put profits from down moves
            max_gain = (entry_price - day_low) / entry_price
            max_loss = (entry_price - day_high) / entry_price
            close_pnl = (entry_price - day_close) / entry_price

            leverage = 2.5
            max_gain *= leverage
            max_loss *= leverage
            close_pnl *= leverage

        trade.high_water_mark = max_gain

        # Determine outcome (order matters - stop checked first)
        if max_loss <= self.HARD_STOP:
            trade.exit_reason = "hard_stop"
            trade.pnl_pct = self.HARD_STOP * 100
        elif max_gain >= self.PROFIT_TARGET:
            trade.exit_reason = "profit_target"
            trade.pnl_pct = self.PROFIT_TARGET * 100
        else:
            trade.exit_reason = "eod_close"
            trade.pnl_pct = close_pnl * 100

        return trade


def run_backtest(days: int = 5, ticker: str = None):
    """Run the full backtest."""
    from mike1.modules.broker_factory import BrokerFactory
    from mike1.modules.judge import Judge
    from mike1.modules.llm_client import get_llm_client

    # Connect to broker
    print("Connecting to Alpaca...")
    broker = BrokerFactory.create("alpaca")
    if not broker.connect():
        print("ERROR: Failed to connect to broker")
        return False

    # Create Judge (without LLM for speed - catalyst scoring skipped)
    print("Initializing Judge (no LLM for speed)...")
    judge = Judge(broker, llm_client=None)

    # Tickers to test
    if ticker:
        tickers = [ticker.upper()]
    else:
        # Default: Test universe from config
        tickers = ["SPY", "QQQ", "NVDA", "TSLA", "AMD", "META"]

    # Run backtest
    backtester = JudgeBacktester(broker, judge, lookback_days=days)
    results = backtester.run(tickers)

    # Print results
    results.print_report()

    return True


def run_from_db():
    """
    Alternative: Analyze actual trades from database.

    This compares the grade given at entry time vs actual P&L outcome.
    """
    import psycopg2

    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("DATABASE_URL not set - cannot analyze historical trades")
        return False

    print("Fetching historical trades from database...")

    conn = psycopg2.connect(db_url)
    cursor = conn.cursor()

    # Get completed trades with grades
    cursor.execute("""
        SELECT
            grade,
            COUNT(*) as count,
            COUNT(*) FILTER (WHERE realized_pnl > 0) as wins,
            ROUND(AVG(pnl_percent)::numeric, 2) as avg_pnl,
            ROUND(SUM(realized_pnl)::numeric, 2) as total_pnl
        FROM trades
        WHERE exit_time IS NOT NULL
          AND grade IS NOT NULL
        GROUP BY grade
        ORDER BY grade
    """)

    rows = cursor.fetchall()
    cursor.close()
    conn.close()

    if not rows:
        print("No completed trades found in database")
        return False

    print()
    print("=" * 60)
    print("ACTUAL TRADE PERFORMANCE BY GRADE")
    print("=" * 60)
    print()
    print(f"{'Grade':<10} {'Count':>6} {'Wins':>6} {'Win %':>8} {'Avg P&L':>10} {'Total P&L':>12}")
    print("-" * 60)

    for row in rows:
        grade, count, wins, avg_pnl, total_pnl = row
        win_pct = (wins / count * 100) if count > 0 else 0
        print(f"{grade:<10} {count:>6} {wins:>6} {win_pct:>7.1f}% {avg_pnl or 0:>9.1f}% ${total_pnl or 0:>10.2f}")

    print()
    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backtest Judge scoring")
    parser.add_argument("--days", type=int, default=5, help="Days to backtest")
    parser.add_argument("--ticker", type=str, help="Single ticker to test")
    parser.add_argument("--from-db", action="store_true", help="Analyze actual trades from database")

    args = parser.parse_args()

    print()
    print("MIKE-1 Judge Backtest")
    print("=" * 60)
    print()

    if args.from_db:
        success = run_from_db()
    else:
        success = run_backtest(days=args.days, ticker=args.ticker)

    sys.exit(0 if success else 1)
