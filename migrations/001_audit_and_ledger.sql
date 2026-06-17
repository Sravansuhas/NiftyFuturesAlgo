-- Aegis: audit + trade ledger (JSONL mirror schema)
-- Apply via: python scripts/db_migrate.py

CREATE TABLE IF NOT EXISTS audit_logs (
    id          BIGSERIAL PRIMARY KEY,
    ts          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ts_epoch    DOUBLE PRECISION NOT NULL,
    event_type  TEXT NOT NULL,
    payload     JSONB NOT NULL DEFAULT '{}',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_audit_logs_ts ON audit_logs (ts DESC);
CREATE INDEX IF NOT EXISTS idx_audit_logs_event_type ON audit_logs (event_type);
CREATE INDEX IF NOT EXISTS idx_audit_logs_ts_epoch ON audit_logs (ts_epoch DESC);

CREATE TABLE IF NOT EXISTS trade_ledger (
    id          BIGSERIAL PRIMARY KEY,
    ts          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ts_epoch    DOUBLE PRECISION NOT NULL,
    event_type  TEXT NOT NULL,
    session_id  TEXT,
    date_ist    DATE NOT NULL,
    payload     JSONB NOT NULL DEFAULT '{}',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_trade_ledger_date_ist ON trade_ledger (date_ist DESC);
CREATE INDEX IF NOT EXISTS idx_trade_ledger_event_type ON trade_ledger (event_type);
CREATE INDEX IF NOT EXISTS idx_trade_ledger_session ON trade_ledger (session_id);
CREATE INDEX IF NOT EXISTS idx_trade_ledger_ts_epoch ON trade_ledger (ts_epoch DESC);