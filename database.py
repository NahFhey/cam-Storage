"""
Database schema and initialization for CAM Tracking Kiosk
"""
import sqlite3
import aiosqlite
from typing import Optional
from contextlib import asynccontextmanager
import config

# SQL schema definition
SCHEMA_SQL = """
-- Jobs table: tracks production jobs by S-number
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    s_number TEXT NOT NULL UNIQUE,
    title TEXT,
    priority_base INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    notes TEXT
);

CREATE INDEX IF NOT EXISTS idx_jobs_s_number ON jobs(s_number);
CREATE INDEX IF NOT EXISTS idx_jobs_priority ON jobs(priority_base DESC);

-- CAM items table: individual cutting tools
CREATE TABLE IF NOT EXISTS cam_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL,
    set_no INTEGER NOT NULL,
    cam_no INTEGER NOT NULL,
    enter_die_steel TEXT,
    exit_die_steel TEXT,
    status_station TEXT NOT NULL CHECK(status_station IN ('active', 'sharpen', 'cabinet', 'refill')),
    status_updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    notes TEXT,
    eol_cycles_expected INTEGER,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE,
    UNIQUE(job_id, set_no, cam_no)
);

CREATE INDEX IF NOT EXISTS idx_cam_items_job ON cam_items(job_id);
CREATE INDEX IF NOT EXISTS idx_cam_items_station ON cam_items(status_station);
CREATE INDEX IF NOT EXISTS idx_cam_items_updated ON cam_items(status_updated_at);

-- Moves table: immutable audit log of all station changes
CREATE TABLE IF NOT EXISTS moves (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cam_item_id INTEGER NOT NULL,
    from_station TEXT CHECK(from_station IN ('active', 'sharpen', 'cabinet', 'refill', 'new')),
    to_station TEXT NOT NULL CHECK(to_station IN ('active', 'sharpen', 'cabinet', 'refill')),
    moved_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    operator TEXT,
    notes TEXT,
    undone BOOLEAN NOT NULL DEFAULT 0,
    FOREIGN KEY (cam_item_id) REFERENCES cam_items(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_moves_cam_item ON moves(cam_item_id);
CREATE INDEX IF NOT EXISTS idx_moves_timestamp ON moves(moved_at DESC);
CREATE INDEX IF NOT EXISTS idx_moves_station ON moves(to_station);
CREATE INDEX IF NOT EXISTS idx_moves_undone ON moves(undone);

-- Configuration table for runtime settings
CREATE TABLE IF NOT EXISTS config (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Insert default config values
INSERT OR IGNORE INTO config (key, value) VALUES ('auto_bump_enabled', 'false');
INSERT OR IGNORE INTO config (key, value) VALUES ('default_operator', 'kiosk');
"""

def init_database(db_path: str = None):
    """Initialize the database with schema (synchronous for setup scripts)"""
    if db_path is None:
        db_path = config.DATABASE_PATH

    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA_SQL)
    conn.commit()
    conn.close()
    print(f"Database initialized at {db_path}")

async def get_db():
    """Get async database connection (for FastAPI dependency injection)"""
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        yield db

@asynccontextmanager
async def get_db_connection():
    """Get async database connection (context manager for manual use)"""
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        yield db

async def get_config_value(key: str, default: str = None) -> Optional[str]:
    """Get configuration value from database"""
    async with get_db_connection() as db:
        cursor = await db.execute("SELECT value FROM config WHERE key = ?", (key,))
        row = await cursor.fetchone()
        return row['value'] if row else default

async def set_config_value(key: str, value: str):
    """Set configuration value in database"""
    async with get_db_connection() as db:
        await db.execute(
            "INSERT OR REPLACE INTO config (key, value, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP)",
            (key, value)
        )
        await db.commit()

if __name__ == "__main__":
    # Allow running this script directly to initialize database
    init_database()
