"""
Database utilities for MIKE-1

Handles connection to NeonDB (PostgreSQL) and common operations.
"""

import os
from typing import Optional, Any
from datetime import datetime, date
import json
import structlog

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor, Json
    HAS_PSYCOPG2 = True
except ImportError:
    HAS_PSYCOPG2 = False

logger = structlog.get_logger()


class Database:
    """
    Database connection manager for MIKE-1.

    Uses NeonDB (PostgreSQL) for persistent storage.
    """

    def __init__(self, database_url: Optional[str] = None):
        if not HAS_PSYCOPG2:
            logger.warning("psycopg2 not installed - database features disabled")
            self.enabled = False
            return

        self.database_url = database_url or os.environ.get("DATABASE_URL")
        self.enabled = bool(self.database_url)
        self._conn = None

        if not self.enabled:
            logger.warning("DATABASE_URL not set - database features disabled")

    def connect(self) -> bool:
        """Establish database connection."""
        if not self.enabled:
            return False

        try:
            self._conn = psycopg2.connect(self.database_url)
            logger.info("Connected to database")
            return True
        except Exception as e:
            logger.error("Database connection failed", error=str(e))
            return False

    def disconnect(self) -> None:
        """Close database connection."""
        if self._conn:
            self._conn.close()
            self._conn = None
            logger.info("Disconnected from database")

    def _execute(self, query: str, params: tuple = None) -> Optional[list]:
        """Execute a query and return results."""
        if not self._conn:
            if not self.connect():
                return None

        try:
            with self._conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(query, params)
                if cur.description:
                    return cur.fetchall()
                self._conn.commit()
                return []
        except Exception as e:
            logger.error("Query failed", error=str(e), query=query[:100])
            self._conn.rollback()
            return None

    def _execute_one(self, query: str, params: tuple = None) -> Optional[dict]:
        """Execute a query and return single result."""
        results = self._execute(query, params)
        if results and len(results) > 0:
            return dict(results[0])
        return None

    # =========================================================================
    # TRADES
    # =========================================================================

    def insert_trade(self, trade_data: dict) -> Optional[str]:
        """Insert a new trade record."""
        query = """
            INSERT INTO trades (
                signal_id, ticker, direction,
                catalyst_type, catalyst_description, catalyst_time,
                grade, score, score_breakdown,
                entry_time, entry_price, contracts, strike, expiration, entry_cost,
                config_version, environment
            ) VALUES (
                %s, %s, %s,
                %s, %s, %s,
                %s, %s, %s,
                %s, %s, %s, %s, %s, %s,
                %s, %s
            )
            RETURNING id
        """

        params = (
            trade_data.get('signal_id'),
            trade_data.get('ticker'),
            trade_data.get('direction'),
            trade_data.get('catalyst_type'),
            trade_data.get('catalyst_description'),
            trade_data.get('catalyst_time'),
            trade_data.get('grade'),
            trade_data.get('score'),
            Json(trade_data.get('score_breakdown', {})),
            trade_data.get('entry_time'),
            trade_data.get('entry_price'),
            trade_data.get('contracts'),
            trade_data.get('strike'),
            trade_data.get('expiration'),
            trade_data.get('entry_cost'),
            trade_data.get('config_version'),
            trade_data.get('environment', 'paper'),
        )

        result = self._execute_one(query, params)
        return str(result['id']) if result else None

    def update_trade_exit(self, trade_id: str, exit_data: dict) -> bool:
        """Update trade with exit information."""
        query = """
            UPDATE trades SET
                exit_time = %s,
                exit_price = %s,
                exit_reason = %s,
                exit_proceeds = %s,
                realized_pnl = %s,
                pnl_percent = %s,
                high_water_mark = %s,
                high_water_pnl_percent = %s
            WHERE id = %s
        """

        params = (
            exit_data.get('exit_time'),
            exit_data.get('exit_price'),
            exit_data.get('exit_reason'),
            exit_data.get('exit_proceeds'),
            exit_data.get('realized_pnl'),
            exit_data.get('pnl_percent'),
            exit_data.get('high_water_mark'),
            exit_data.get('high_water_pnl_percent'),
            trade_id,
        )

        result = self._execute(query, params)
        return result is not None

    def update_trade_trim(self, trade_id: str, trim_number: int, trim_data: dict) -> bool:
        """Update trade with trim information."""
        if trim_number == 1:
            query = """
                UPDATE trades SET
                    trim_1_time = %s,
                    trim_1_price = %s,
                    trim_1_contracts = %s,
                    trim_1_pnl = %s
                WHERE id = %s
            """
        else:
            query = """
                UPDATE trades SET
                    trim_2_time = %s,
                    trim_2_price = %s,
                    trim_2_contracts = %s,
                    trim_2_pnl = %s
                WHERE id = %s
            """

        params = (
            trim_data.get('time'),
            trim_data.get('price'),
            trim_data.get('contracts'),
            trim_data.get('pnl'),
            trade_id,
        )

        result = self._execute(query, params)
        return result is not None

    def get_trades(
        self,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        ticker: Optional[str] = None,
        grade: Optional[str] = None,
        limit: int = 100
    ) -> list[dict]:
        """Get trades with optional filters."""
        conditions = []
        params = []

        if start_date:
            conditions.append("entry_time >= %s")
            params.append(start_date)

        if end_date:
            conditions.append("entry_time <= %s")
            params.append(end_date)

        if ticker:
            conditions.append("ticker = %s")
            params.append(ticker)

        if grade:
            conditions.append("grade = %s")
            params.append(grade)

        where_clause = " AND ".join(conditions) if conditions else "1=1"

        query = f"""
            SELECT * FROM trades
            WHERE {where_clause}
            ORDER BY entry_time DESC
            LIMIT %s
        """
        params.append(limit)

        results = self._execute(query, tuple(params))
        return [dict(r) for r in results] if results else []

    # =========================================================================
    # ACTIONS
    # =========================================================================

    def insert_action(self, action_data: dict) -> Optional[str]:
        """Insert an action log entry."""
        query = """
            INSERT INTO actions (
                action_type, trade_id, position_id, ticker, details, dry_run, timestamp
            ) VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING id
        """

        params = (
            action_data.get('action_type'),
            action_data.get('trade_id'),
            action_data.get('position_id'),
            action_data.get('ticker'),
            Json(action_data.get('details', {})),
            action_data.get('dry_run', False),
            action_data.get('timestamp', datetime.now()),
        )

        result = self._execute_one(query, params)
        return str(result['id']) if result else None

    # =========================================================================
    # SIGNALS
    # =========================================================================

    def insert_signal(self, signal_data: dict) -> Optional[str]:
        """Insert a signal record."""
        query = """
            INSERT INTO signals (
                signal_id, ticker, direction,
                catalyst_type, catalyst_description, catalyst_time,
                stock_price, vwap, volume, avg_volume, rsi,
                score, grade, score_breakdown, score_reasons,
                was_traded, rejection_reason
            ) VALUES (
                %s, %s, %s,
                %s, %s, %s,
                %s, %s, %s, %s, %s,
                %s, %s, %s, %s,
                %s, %s
            )
            RETURNING id
        """

        params = (
            signal_data.get('signal_id'),
            signal_data.get('ticker'),
            signal_data.get('direction'),
            signal_data.get('catalyst_type'),
            signal_data.get('catalyst_description'),
            signal_data.get('catalyst_time'),
            signal_data.get('stock_price'),
            signal_data.get('vwap'),
            signal_data.get('volume'),
            signal_data.get('avg_volume'),
            signal_data.get('rsi'),
            signal_data.get('score'),
            signal_data.get('grade'),
            Json(signal_data.get('score_breakdown', {})),
            signal_data.get('score_reasons', []),
            signal_data.get('was_traded', False),
            signal_data.get('rejection_reason'),
        )

        result = self._execute_one(query, params)
        return str(result['id']) if result else None

    # =========================================================================
    # DAILY STATS
    # =========================================================================

    def upsert_daily_stats(self, stats: dict) -> bool:
        """Update or insert daily statistics."""
        query = """
            INSERT INTO daily_stats (
                trade_date, trades_executed, trades_won, trades_lost,
                realized_pnl, gross_profit, gross_loss,
                win_rate, avg_win, avg_loss, profit_factor,
                a_trades, a_wins, a_pnl,
                b_trades, b_wins, b_pnl,
                hard_stops, trailing_stops, dte_closes, lockouts
            ) VALUES (
                %s, %s, %s, %s,
                %s, %s, %s,
                %s, %s, %s, %s,
                %s, %s, %s,
                %s, %s, %s,
                %s, %s, %s, %s
            )
            ON CONFLICT (trade_date) DO UPDATE SET
                trades_executed = EXCLUDED.trades_executed,
                trades_won = EXCLUDED.trades_won,
                trades_lost = EXCLUDED.trades_lost,
                realized_pnl = EXCLUDED.realized_pnl,
                gross_profit = EXCLUDED.gross_profit,
                gross_loss = EXCLUDED.gross_loss,
                win_rate = EXCLUDED.win_rate,
                avg_win = EXCLUDED.avg_win,
                avg_loss = EXCLUDED.avg_loss,
                profit_factor = EXCLUDED.profit_factor,
                a_trades = EXCLUDED.a_trades,
                a_wins = EXCLUDED.a_wins,
                a_pnl = EXCLUDED.a_pnl,
                b_trades = EXCLUDED.b_trades,
                b_wins = EXCLUDED.b_wins,
                b_pnl = EXCLUDED.b_pnl,
                hard_stops = EXCLUDED.hard_stops,
                trailing_stops = EXCLUDED.trailing_stops,
                dte_closes = EXCLUDED.dte_closes,
                lockouts = EXCLUDED.lockouts
        """

        params = (
            stats.get('trade_date'),
            stats.get('trades_executed', 0),
            stats.get('trades_won', 0),
            stats.get('trades_lost', 0),
            stats.get('realized_pnl', 0),
            stats.get('gross_profit', 0),
            stats.get('gross_loss', 0),
            stats.get('win_rate'),
            stats.get('avg_win'),
            stats.get('avg_loss'),
            stats.get('profit_factor'),
            stats.get('a_trades', 0),
            stats.get('a_wins', 0),
            stats.get('a_pnl', 0),
            stats.get('b_trades', 0),
            stats.get('b_wins', 0),
            stats.get('b_pnl', 0),
            stats.get('hard_stops', 0),
            stats.get('trailing_stops', 0),
            stats.get('dte_closes', 0),
            stats.get('lockouts', 0),
        )

        result = self._execute(query, params)
        return result is not None

    def get_daily_stats(self, trade_date: date) -> Optional[dict]:
        """Get stats for a specific date."""
        query = "SELECT * FROM daily_stats WHERE trade_date = %s"
        return self._execute_one(query, (trade_date,))

    # =========================================================================
    # SYSTEM EVENTS
    # =========================================================================

    def insert_system_event(self, event_type: str, details: dict = None) -> Optional[str]:
        """Insert a system event."""
        query = """
            INSERT INTO system_events (event_type, details)
            VALUES (%s, %s)
            RETURNING id
        """

        result = self._execute_one(query, (event_type, Json(details or {})))
        return str(result['id']) if result else None

    # =========================================================================
    # ANALYTICS
    # =========================================================================

    def get_performance_by_grade(self) -> list[dict]:
        """Get performance breakdown by grade."""
        query = "SELECT * FROM performance_by_grade"
        results = self._execute(query)
        return [dict(r) for r in results] if results else []

    def get_performance_by_ticker(self) -> list[dict]:
        """Get performance breakdown by ticker."""
        query = "SELECT * FROM performance_by_ticker"
        results = self._execute(query)
        return [dict(r) for r in results] if results else []

    def get_exit_analysis(self) -> list[dict]:
        """Get exit reason analysis."""
        query = "SELECT * FROM exit_analysis"
        results = self._execute(query)
        return [dict(r) for r in results] if results else []

    def get_recent_trades(self, limit: int = 50) -> list[dict]:
        """Get recent trades."""
        query = f"SELECT * FROM recent_trades LIMIT {limit}"
        results = self._execute(query)
        return [dict(r) for r in results] if results else []


# Global database instance
_db: Optional[Database] = None


def get_db() -> Database:
    """Get the global database instance."""
    global _db
    if _db is None:
        _db = Database()
    return _db


def init_schema(database_url: str) -> bool:
    """
    Initialize the database schema.

    Run this once to set up the tables.
    """
    import os
    from pathlib import Path

    schema_path = Path(__file__).parent.parent.parent.parent.parent / "db" / "schema.sql"

    if not schema_path.exists():
        logger.error("Schema file not found", path=str(schema_path))
        return False

    db = Database(database_url)
    if not db.connect():
        return False

    try:
        with open(schema_path, 'r') as f:
            schema_sql = f.read()

        with db._conn.cursor() as cur:
            cur.execute(schema_sql)
            db._conn.commit()

        logger.info("Database schema initialized successfully")
        return True

    except Exception as e:
        logger.error("Failed to initialize schema", error=str(e))
        return False

    finally:
        db.disconnect()
