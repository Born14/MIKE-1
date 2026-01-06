"""
Configuration Management for MIKE-1

All trading logic is driven by configuration.
No hard-coded numbers. Ever.
"""

import os
from pathlib import Path
from typing import Any, Optional
import yaml
from pydantic import BaseModel, Field


class RiskConfig(BaseModel):
    """Risk limits - The Governor's rules."""
    max_risk_per_trade: float = 200
    max_contracts: int = 1
    max_trades_per_day: int = 2
    max_daily_loss: float = 100
    kill_switch: bool = False


class TrimConfig(BaseModel):
    """Individual trim level configuration."""
    trigger_pct: float
    sell_pct: float


class AtrTrailingConfig(BaseModel):
    """ATR-based trailing stop configuration."""
    enabled: bool = True          # Use ATR trailing for single contracts
    multiplier: float = 2.0       # Stop distance = ATR * multiplier * delta
    period: int = 14              # ATR lookback period (days)


class ExitConfig(BaseModel):
    """Exit rules - Non-negotiable."""
    trim_1: TrimConfig = Field(default_factory=lambda: TrimConfig(trigger_pct=25, sell_pct=50))
    trim_2: TrimConfig = Field(default_factory=lambda: TrimConfig(trigger_pct=50, sell_pct=100))
    trailing_stop_pct: float = 25
    hard_stop_pct: float = 50
    atr_trailing: AtrTrailingConfig = Field(default_factory=AtrTrailingConfig)
    close_at_dte: int = 1
    force_close_0dte_time: str = "15:30"  # Force close 0DTE at this time (ET)


class DeltaRange(BaseModel):
    """Delta targeting for option selection."""
    delta_min: float
    delta_max: float


class OptionsConfig(BaseModel):
    """Option selection rules."""
    a_tier: DeltaRange = Field(default_factory=lambda: DeltaRange(delta_min=0.30, delta_max=0.45))
    b_tier: DeltaRange = Field(default_factory=lambda: DeltaRange(delta_min=0.15, delta_max=0.30))
    min_dte: int = 3
    max_dte: int = 14
    min_open_interest: int = 0  # Alpaca paper doesn't provide OI
    min_stock_volume: int = 1000000  # Min daily volume for liquid options (1M)
    max_bid_ask_spread_pct: float = 0.10


class ScoringCriterion(BaseModel):
    """Individual scoring criterion."""
    description: str
    points: int


class ScoringConfig(BaseModel):
    """The Judge's scoring system."""
    min_trade_grade: str = "A"  # "A" = A-TIER only, "B" = A+B, "N" = all
    a_tier_min: float = 7.0
    b_tier_min: float = 5.0
    criteria: dict[str, ScoringCriterion] = Field(default_factory=dict)


class ReentryConfig(BaseModel):
    """Re-entry rules."""
    enabled: bool = True
    max_reentries: int = 2
    conditions: dict[str, bool] = Field(default_factory=dict)
    sizing: str = "same"


class ManualBasketSource(BaseModel):
    """Manual ticker input from file."""
    enabled: bool = True
    file: str = "data/manual_tickers.txt"
    max_age_hours: int = 24


class CoreBasketSource(BaseModel):
    """Core watchlist - always monitored."""
    enabled: bool = True
    tickers: list[str] = Field(default_factory=lambda: ["SPY", "QQQ", "NVDA", "TSLA"])


class CategoriesBasketSource(BaseModel):
    """Category-based watchlists."""
    enabled: bool = True
    tech: list[str] = Field(default_factory=list)
    biotech: list[str] = Field(default_factory=list)
    momentum: list[str] = Field(default_factory=list)
    etfs: list[str] = Field(default_factory=list)


class ScreenerBasketSource(BaseModel):
    """Auto-screener results (future)."""
    enabled: bool = False
    max_tickers: int = 50


class BasketConfig(BaseModel):
    """Ticker sources for Scout scanning."""
    manual: ManualBasketSource = Field(default_factory=ManualBasketSource)
    core: CoreBasketSource = Field(default_factory=CoreBasketSource)
    categories: CategoriesBasketSource = Field(default_factory=CategoriesBasketSource)
    screener: ScreenerBasketSource = Field(default_factory=ScreenerBasketSource)
    deduplicate: bool = True

    @property
    def all_tickers(self) -> list[str]:
        """
        Get flat list of all tickers from all enabled sources.

        Order of priority:
        1. Manual file
        2. Core watchlist
        3. Category watchlists
        4. Screener results
        """
        tickers = []

        # Source 1: Manual (from file)
        if self.manual.enabled:
            manual_tickers = self._read_manual_file()
            tickers.extend(manual_tickers)

        # Source 2: Core
        if self.core.enabled:
            tickers.extend(self.core.tickers)

        # Source 3: Categories
        if self.categories.enabled:
            tickers.extend(self.categories.tech)
            tickers.extend(self.categories.biotech)
            tickers.extend(self.categories.momentum)
            tickers.extend(self.categories.etfs)

        # Source 4: Screener (future)
        # if self.screener.enabled:
        #     tickers.extend(self._get_screener_results())

        # Deduplicate if enabled
        if self.deduplicate:
            return list(dict.fromkeys(tickers))  # Preserves order

        return tickers

    def _read_manual_file(self) -> list[str]:
        """Read tickers from manual input file."""
        from pathlib import Path
        from datetime import datetime, timedelta
        import os

        file_path = Path(self.manual.file)

        # If path is relative, resolve from project root
        if not file_path.is_absolute():
            # Look for file relative to config directory
            search_paths = [
                file_path,  # Current directory
                Path("..") / file_path,  # Parent directory (if running from engine/)
                Path(os.environ.get("MIKE1_ROOT", ".")) / file_path,  # Project root
            ]

            # Find first existing file
            for candidate in search_paths:
                if candidate.exists():
                    file_path = candidate
                    break

        # Check if file exists
        if not file_path.exists():
            return []

        # Check file age
        file_age = datetime.now() - datetime.fromtimestamp(file_path.stat().st_mtime)
        max_age = timedelta(hours=self.manual.max_age_hours)

        if file_age > max_age:
            # File too old, ignore it
            return []

        # Read tickers (one per line, skip comments and empty lines)
        with open(file_path, 'r') as f:
            tickers = []
            for line in f:
                line = line.strip()
                # Skip empty lines and comments
                if line and not line.startswith('#'):
                    tickers.append(line.upper())

        return tickers


class NotificationsConfig(BaseModel):
    """Notification settings."""
    enabled: bool = True
    channels: list[str] = Field(default_factory=lambda: ["console"])
    events: dict[str, bool] = Field(default_factory=dict)


class LoggingConfig(BaseModel):
    """Logging settings."""
    level: str = "INFO"
    log_signals: bool = True
    log_grades: bool = True
    log_entries: bool = True
    log_exits: bool = True
    log_pnl: bool = True
    retain_days: int = 365


class CuratorConfig(BaseModel):
    """Curator (option chain selection) settings."""
    max_candidates: int = 3
    ideal_delta: float = 0.375  # Midpoint of A-tier range (0.30-0.45)
    unusual_activity_threshold: float = 1.25  # Vol/OI ratio to trigger UOA
    unusual_activity_boost: float = 20.0
    cache_chain_seconds: int = 60


class EngineConfig(BaseModel):
    """Engine runtime settings."""
    poll_interval: int = 30
    market_open: str = "09:30"
    market_close: str = "16:00"
    allow_premarket: bool = False
    allow_afterhours: bool = False


class Config(BaseModel):
    """
    Master configuration for MIKE-1.

    This is the single source of truth for all trading behavior.
    """
    version: str = "1.0.0"
    environment: str = "paper"  # paper | live
    armed: bool = False  # Master switch

    risk: RiskConfig = Field(default_factory=RiskConfig)
    exits: ExitConfig = Field(default_factory=ExitConfig)
    options: OptionsConfig = Field(default_factory=OptionsConfig)
    curator: CuratorConfig = Field(default_factory=CuratorConfig)
    scoring: ScoringConfig = Field(default_factory=ScoringConfig)
    reentry: ReentryConfig = Field(default_factory=ReentryConfig)
    basket: BasketConfig = Field(default_factory=BasketConfig)
    notifications: NotificationsConfig = Field(default_factory=NotificationsConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    engine: EngineConfig = Field(default_factory=EngineConfig)

    @classmethod
    def load(cls, config_path: Optional[str] = None) -> "Config":
        """
        Load configuration from YAML file.

        Args:
            config_path: Path to config file. If None, uses default.yaml

        Returns:
            Loaded Config instance
        """
        if config_path is None:
            # Look for config in standard locations
            search_paths = [
                Path("config/default.yaml"),
                Path("../config/default.yaml"),
                Path(os.environ.get("MIKE1_CONFIG", "config/default.yaml")),
            ]

            for path in search_paths:
                if path.exists():
                    config_path = str(path)
                    break
            else:
                # Return defaults if no config found
                return cls()

        with open(config_path, "r") as f:
            data = yaml.safe_load(f)

        return cls.model_validate(data)

    def reload(self, config_path: str) -> "Config":
        """Hot-reload configuration from file."""
        return self.load(config_path)

    def is_armed(self) -> bool:
        """Check if system is armed for live trading."""
        return self.armed and not self.risk.kill_switch

    def is_live(self) -> bool:
        """Check if running in live mode."""
        return self.environment == "live"

    def can_trade(self) -> bool:
        """Check if trading is allowed right now."""
        return self.is_armed() and not self.risk.kill_switch


# Global config instance (loaded on import)
_config: Optional[Config] = None


def get_config() -> Config:
    """Get the global configuration instance."""
    global _config
    if _config is None:
        _config = Config.load()
    return _config


def reload_config(config_path: Optional[str] = None) -> Config:
    """Reload configuration from file."""
    global _config
    _config = Config.load(config_path)
    return _config
