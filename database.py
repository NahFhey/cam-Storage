"""
Database schema and initialization for CAM Tracking Kiosk
"""
import sqlite3
import aiosqlite
import logging
from typing import Optional
from contextlib import asynccontextmanager
import config

# Set up logger
logger = logging.getLogger(__name__)

# SQL schema definition
SCHEMA_SQL = """
-- Jobs table: tracks production jobs by S-number
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    s_number TEXT NOT NULL UNIQUE,
    title TEXT,
    priority_level TEXT NOT NULL DEFAULT 'low' CHECK(priority_level IN ('low', 'medium', 'high', 'urgent', 'top')),
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    notes TEXT
);

CREATE INDEX IF NOT EXISTS idx_jobs_s_number ON jobs(s_number);
CREATE INDEX IF NOT EXISTS idx_jobs_priority ON jobs(priority_level);

-- CAM items table: individual cutting tools
CREATE TABLE IF NOT EXISTS cam_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL,
    set_no INTEGER NOT NULL,
    cam_no INTEGER NOT NULL,
    die_position TEXT CHECK(die_position IN ('upper', 'lower')),
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
CREATE INDEX IF NOT EXISTS idx_cam_items_die_position ON cam_items(die_position);

-- Moves table: immutable audit log of all station changes
CREATE TABLE IF NOT EXISTS moves (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cam_item_id INTEGER NOT NULL,
    from_station TEXT CHECK(from_station IN ('active', 'sharpen', 'cabinet', 'refill', 'new')),
    to_station TEXT NOT NULL CHECK(to_station IN ('active', 'sharpen', 'cabinet', 'refill')),
    moved_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    operator TEXT,
    notes TEXT,
    material_removed REAL,
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
INSERT OR IGNORE INTO config (key, value) VALUES ('auto_bump_enabled', 'true');
INSERT OR IGNORE INTO config (key, value) VALUES ('default_operator', 'kiosk');
"""

def init_database(db_path: str = None):
    """Initialize the database with schema (synchronous for setup scripts)"""
    if db_path is None:
        db_path = config.DATABASE_PATH

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA_SQL)
    conn.commit()
    conn.close()
    logger.info(f"Database initialized at {db_path}")

async def get_db():
    """Get async database connection (for FastAPI dependency injection)"""
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA foreign_keys = ON")
        yield db

@asynccontextmanager
async def get_db_connection():
    """Get async database connection (context manager for manual use)"""
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA foreign_keys = ON")
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

def migrate_database(db_path: str = None):
    """Migrate existing database to new schema"""
    if db_path is None:
        db_path = config.DATABASE_PATH

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    cursor = conn.cursor()

    logger.info("Checking for database migrations...")

    # Check if priority_base exists (old schema)
    cursor.execute("PRAGMA table_info(jobs)")
    columns = [col[1] for col in cursor.fetchall()]

    if 'priority_base' in columns:
        logger.info("  Migrating jobs table: priority_base -> priority_level...")
        # Add new column
        try:
            cursor.execute("ALTER TABLE jobs ADD COLUMN priority_level TEXT DEFAULT 'low'")
        except sqlite3.OperationalError:
            logger.warning("    Column priority_level already exists, skipping")

        # Migrate data: 0=low, 1=medium, 2=high, 3+=urgent
        cursor.execute("""
            UPDATE jobs SET priority_level =
                CASE
                    WHEN priority_base >= 3 THEN 'urgent'
                    WHEN priority_base = 2 THEN 'high'
                    WHEN priority_base = 1 THEN 'medium'
                    ELSE 'low'
                END
            WHERE priority_level IS NULL OR priority_level = 'low'
        """)
        logger.info("    Migrated priority values")

    # Check if die_position exists
    cursor.execute("PRAGMA table_info(cam_items)")
    columns = [col[1] for col in cursor.fetchall()]

    if 'die_position' not in columns:
        logger.info("  Adding die_position column to cam_items...")
        cursor.execute("ALTER TABLE cam_items ADD COLUMN die_position TEXT")
        logger.info("    Added die_position column (defaults to NULL)")

    # Check if material_removed exists
    cursor.execute("PRAGMA table_info(moves)")
    columns = [col[1] for col in cursor.fetchall()]

    if 'material_removed' not in columns:
        logger.info("  Adding material_removed column to moves...")
        cursor.execute("ALTER TABLE moves ADD COLUMN material_removed REAL")
        logger.info("    Added material_removed column (defaults to NULL)")

    conn.commit()
    conn.close()
    logger.info("✓ Database migration complete")

if __name__ == "__main__":
    # Allow running this script directly to initialize database
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == 'migrate':
        migrate_database()
    else:
        init_database()
