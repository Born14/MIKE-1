-- MIKE-1 Database Schema for NeonDB (PostgreSQL)
-- This is the source of truth for all trade data

-- =============================================================================
-- TRADES TABLE
-- =============================================================================
-- Complete record of every trade

CREATE TABLE IF NOT EXISTS trades (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Signal Info
    signal_id VARCHAR(100) NOT NULL,
    ticker VARCHAR(10) NOT NULL,
    direction VARCHAR(10) NOT NULL CHECK (direction IN ('call', 'put')),

    -- Catalyst
    catalyst_type VARCHAR(50),
    catalyst_description TEXT,
    catalyst_time TIMESTAMPTZ,

    -- Grading
    grade VARCHAR(10) NOT NULL CHECK (grade IN ('A', 'B', 'NO_TRADE')),
    score INTEGER,
    score_breakdown JSONB DEFAULT '{}',

    -- Entry
    entry_time TIMESTAMPTZ NOT NULL,
    entry_price DECIMAL(10, 4) NOT NULL,
    contracts INTEGER NOT NULL,
    strike DECIMAL(10, 2) NOT NULL,
    expiration DATE NOT NULL,
    entry_cost DECIMAL(12, 2) NOT NULL,

    -- Exit
    exit_time TIMESTAMPTZ,
    exit_price DECIMAL(10, 4),
    exit_reason VARCHAR(50),
    exit_proceeds DECIMAL(12, 2),

    -- P&L
    realized_pnl DECIMAL(12, 2),
    pnl_percent DECIMAL(8, 4),
    high_water_mark DECIMAL(10, 4),
    high_water_pnl_percent DECIMAL(8, 4),

    -- Trims
    trim_1_time TIMESTAMPTZ,
    trim_1_price DECIMAL(10, 4),
    trim_1_contracts INTEGER,
    trim_1_pnl DECIMAL(12, 2),

    trim_2_time TIMESTAMPTZ,
    trim_2_price DECIMAL(10, 4),
    trim_2_contracts INTEGER,
    trim_2_pnl DECIMAL(12, 2),

    -- Meta
    config_version VARCHAR(20),
    environment VARCHAR(20) DEFAULT 'paper',
    notes TEXT,
    tags TEXT[],

    -- Timestamps
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes for common queries
CREATE INDEX idx_trades_ticker ON trades(ticker);
CREATE INDEX idx_trades_entry_time ON trades(entry_time);
CREATE INDEX idx_trades_grade ON trades(grade);
CREATE INDEX idx_trades_exit_reason ON trades(exit_reason);

-- =============================================================================
-- ACTIONS TABLE
-- =============================================================================
-- Log of all executor actions

CREATE TABLE IF NOT EXISTS actions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Action Info
    action_type VARCHAR(50) NOT NULL,
    trade_id UUID REFERENCES trades(id),
    position_id VARCHAR(100),
    ticker VARCHAR(10),

    -- Details
    details JSONB DEFAULT '{}',
    dry_run BOOLEAN DEFAULT FALSE,

    -- Timestamps
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_actions_type ON actions(action_type);
CREATE INDEX idx_actions_timestamp ON actions(timestamp);
CREATE INDEX idx_actions_trade_id ON actions(trade_id);

-- =============================================================================
-- SIGNALS TABLE
-- =============================================================================
-- Record of all detected signals (even ones not traded)

CREATE TABLE IF NOT EXISTS signals (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Signal Info
    signal_id VARCHAR(100) NOT NULL,
    ticker VARCHAR(10) NOT NULL,
    direction VARCHAR(10) NOT NULL,

    -- Catalyst
    catalyst_type VARCHAR(50),
    catalyst_description TEXT,
    catalyst_time TIMESTAMPTZ,

    -- Market State
    stock_price DECIMAL(10, 2),
    vwap DECIMAL(10, 2),
    volume BIGINT,
    avg_volume BIGINT,
    rsi DECIMAL(5, 2),

    -- Scoring
    score INTEGER,
    grade VARCHAR(10),
    score_breakdown JSONB DEFAULT '{}',
    score_reasons TEXT[],

    -- Outcome
    was_traded BOOLEAN DEFAULT FALSE,
    trade_id UUID REFERENCES trades(id),
    rejection_reason TEXT,

    -- Timestamps
    detected_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_signals_ticker ON signals(ticker);
CREATE INDEX idx_signals_detected_at ON signals(detected_at);
CREATE INDEX idx_signals_was_traded ON signals(was_traded);

-- =============================================================================
-- DAILY_STATS TABLE
-- =============================================================================
-- Aggregated daily statistics

CREATE TABLE IF NOT EXISTS daily_stats (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Date
    trade_date DATE NOT NULL UNIQUE,

    -- Trade counts
    trades_executed INTEGER DEFAULT 0,
    trades_won INTEGER DEFAULT 0,
    trades_lost INTEGER DEFAULT 0,

    -- P&L
    realized_pnl DECIMAL(12, 2) DEFAULT 0,
    gross_profit DECIMAL(12, 2) DEFAULT 0,
    gross_loss DECIMAL(12, 2) DEFAULT 0,

    -- Metrics
    win_rate DECIMAL(5, 2),
    avg_win DECIMAL(12, 2),
    avg_loss DECIMAL(12, 2),
    profit_factor DECIMAL(6, 2),

    -- By Grade
    a_trades INTEGER DEFAULT 0,
    a_wins INTEGER DEFAULT 0,
    a_pnl DECIMAL(12, 2) DEFAULT 0,

    b_trades INTEGER DEFAULT 0,
    b_wins INTEGER DEFAULT 0,
    b_pnl DECIMAL(12, 2) DEFAULT 0,

    -- Risk Events
    hard_stops INTEGER DEFAULT 0,
    trailing_stops INTEGER DEFAULT 0,
    dte_closes INTEGER DEFAULT 0,
    lockouts INTEGER DEFAULT 0,

    -- Timestamps
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_daily_stats_date ON daily_stats(trade_date);

-- =============================================================================
-- CONFIG_SNAPSHOTS TABLE
-- =============================================================================
-- Record of config at time of trades (for analysis)

CREATE TABLE IF NOT EXISTS config_snapshots (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Version
    version VARCHAR(20) NOT NULL,

    -- Full config
    config JSONB NOT NULL,

    -- When used
    active_from TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    active_until TIMESTAMPTZ,

    -- Timestamps
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_config_snapshots_active_from ON config_snapshots(active_from);

-- =============================================================================
-- SYSTEM_EVENTS TABLE
-- =============================================================================
-- System-level events (starts, stops, errors, kills)

CREATE TABLE IF NOT EXISTS system_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Event Info
    event_type VARCHAR(50) NOT NULL,
    details JSONB DEFAULT '{}',

    -- Timestamps
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_system_events_type ON system_events(event_type);
CREATE INDEX idx_system_events_timestamp ON system_events(timestamp);

-- =============================================================================
-- VIEWS
-- =============================================================================

-- Recent trades view
CREATE OR REPLACE VIEW recent_trades AS
SELECT
    t.id,
    t.ticker,
    t.direction,
    t.grade,
    t.entry_time,
    t.entry_price,
    t.contracts,
    t.exit_time,
    t.exit_price,
    t.exit_reason,
    t.realized_pnl,
    t.pnl_percent,
    EXTRACT(EPOCH FROM (COALESCE(t.exit_time, NOW()) - t.entry_time)) / 60 as hold_time_minutes
FROM trades t
ORDER BY t.entry_time DESC
LIMIT 50;

-- Performance by grade
CREATE OR REPLACE VIEW performance_by_grade AS
SELECT
    grade,
    COUNT(*) as total_trades,
    COUNT(*) FILTER (WHERE realized_pnl > 0) as wins,
    COUNT(*) FILTER (WHERE realized_pnl < 0) as losses,
    ROUND(100.0 * COUNT(*) FILTER (WHERE realized_pnl > 0) / NULLIF(COUNT(*), 0), 2) as win_rate,
    SUM(realized_pnl) as total_pnl,
    ROUND(AVG(realized_pnl), 2) as avg_pnl,
    ROUND(AVG(realized_pnl) FILTER (WHERE realized_pnl > 0), 2) as avg_win,
    ROUND(AVG(realized_pnl) FILTER (WHERE realized_pnl < 0), 2) as avg_loss
FROM trades
WHERE exit_time IS NOT NULL
GROUP BY grade;

-- Performance by ticker
CREATE OR REPLACE VIEW performance_by_ticker AS
SELECT
    ticker,
    COUNT(*) as total_trades,
    COUNT(*) FILTER (WHERE realized_pnl > 0) as wins,
    ROUND(100.0 * COUNT(*) FILTER (WHERE realized_pnl > 0) / NULLIF(COUNT(*), 0), 2) as win_rate,
    SUM(realized_pnl) as total_pnl,
    ROUND(AVG(pnl_percent), 2) as avg_pnl_percent
FROM trades
WHERE exit_time IS NOT NULL
GROUP BY ticker
ORDER BY total_pnl DESC;

-- Exit reason analysis
CREATE OR REPLACE VIEW exit_analysis AS
SELECT
    exit_reason,
    COUNT(*) as count,
    SUM(realized_pnl) as total_pnl,
    ROUND(AVG(realized_pnl), 2) as avg_pnl,
    ROUND(AVG(pnl_percent), 2) as avg_pnl_percent
FROM trades
WHERE exit_reason IS NOT NULL
GROUP BY exit_reason
ORDER BY count DESC;

-- =============================================================================
-- FUNCTIONS
-- =============================================================================

-- Function to update updated_at timestamp
CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Trigger for trades
CREATE TRIGGER trades_updated_at
    BEFORE UPDATE ON trades
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at();

-- Trigger for daily_stats
CREATE TRIGGER daily_stats_updated_at
    BEFORE UPDATE ON daily_stats
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at();
