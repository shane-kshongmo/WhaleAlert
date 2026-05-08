-- Whale Alert Service - Supabase PostgreSQL Schema
-- Generated from SQLite schema
-- Run this in Supabase Dashboard → SQL Editor

-- Enable UUID extension (useful for future features)
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Snapshots table
CREATE TABLE IF NOT EXISTS snapshots (
    id BIGINT PRIMARY KEY,
    timestamp BIGINT NOT NULL,
    symbol TEXT NOT NULL,
    price REAL NOT NULL,
    volume_24h REAL NOT NULL,
    price_change_24h REAL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_snapshots_timestamp ON snapshots(timestamp);
CREATE INDEX IF NOT EXISTS idx_snapshots_symbol ON snapshots(symbol);

-- Paper trades table
CREATE TABLE IF NOT EXISTS paper_trades (
    id INTEGER PRIMARY KEY GENERATED ALWAYS AS IDENTITY,
    entry_time BIGINT NOT NULL,
    exit_time BIGINT,
    symbol TEXT NOT NULL,
    entry_price REAL NOT NULL,
    exit_price REAL,
    quantity REAL NOT NULL,
    pnl_usd REAL DEFAULT 0,
    pnl_pct REAL DEFAULT 0,
    alert_score INTEGER,
    close_reason TEXT,
    status TEXT NOT NULL DEFAULT 'open',
    tier TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_paper_trades_symbol ON paper_trades(symbol);
CREATE INDEX IF NOT EXISTS idx_paper_trades_status ON paper_trades(status);
CREATE INDEX IF NOT EXISTS idx_paper_trades_entry_time ON paper_trades(entry_time);

-- ML training samples table
CREATE TABLE IF NOT EXISTS ml_training_samples (
    id BIGINT PRIMARY KEY GENERATED ALWAYS AS IDENTITY,
    snapshot_id BIGINT NOT NULL,
    symbol TEXT NOT NULL,
    features JSONB NOT NULL,
    label INTEGER,
    label_verified BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_ml_samples_symbol ON ml_training_samples(symbol);
CREATE INDEX IF NOT EXISTS idx_ml_samples_label_verified ON ml_training_samples(label_verified);

-- Pending ML samples table
CREATE TABLE IF NOT EXISTS pending_ml_samples (
    id BIGINT PRIMARY KEY GENERATED ALWAYS AS IDENTITY,
    snapshot_id BIGINT NOT NULL,
    symbol TEXT NOT NULL,
    alert_score INTEGER NOT NULL,
    features JSONB NOT NULL,
    created_at BIGINT NOT NULL,
    label_check_time BIGINT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_pending_created_at ON pending_ml_samples(created_at);

-- Pump events table
CREATE TABLE IF NOT EXISTS pump_events (
    id BIGINT PRIMARY KEY GENERATED ALWAYS AS IDENTITY,
    symbol TEXT NOT NULL,
    detected_at BIGINT NOT NULL,
    initial_price REAL NOT NULL,
    peak_price REAL,
    peak_pct_change REAL,
    volume_avg REAL,
    volume_peak REAL,
    status TEXT DEFAULT 'active',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_pump_events_detected_at ON pump_events(detected_at);

-- Add comments for documentation
COMMENT ON TABLE snapshots IS 'Market snapshots collected every 15 minutes';
COMMENT ON TABLE paper_trades IS 'Paper trading records with PnL tracking';
COMMENT ON TABLE ml_training_samples IS 'ML model training data with verified labels';
COMMENT ON TABLE pending_ml_samples IS 'Samples awaiting 24h forward price verification';
COMMENT ON TABLE pump_events IS 'Detected pump events (20% gain + 1.5x volume)';
