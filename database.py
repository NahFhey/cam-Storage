"""
Database schema and initialization for CAM Tracking Kiosk
"""
import sqlite3
import asyncio
import aiosqlite
import hashlib
import secrets
import logging
from typing import Optional
from contextlib import asynccontextmanager
import config

# Set up logger
logger = logging.getLogger(__name__)

# ========== Connection Pool ==========

class ConnectionPool:
    """Simple async SQLite connection pool to avoid per-request connection overhead."""

    def __init__(self, db_path: str, max_size: int = 5):
        self._db_path = db_path
        self._max_size = max_size
        self._pool: asyncio.Queue = asyncio.Queue(maxsize=max_size)
        self._size = 0
        self._lock = asyncio.Lock()

    async def _create_connection(self) -> aiosqlite.Connection:
        conn = await aiosqlite.connect(self._db_path)
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA foreign_keys = ON")
        return conn

    async def acquire(self) -> aiosqlite.Connection:
        # Try to get an existing connection from the pool
        try:
            conn = self._pool.get_nowait()
            # Verify the connection is still usable
            try:
                await conn.execute("SELECT 1")
                return conn
            except Exception:
                async with self._lock:
                    self._size -= 1
        except asyncio.QueueEmpty:
            pass

        # Create a new connection if under limit
        async with self._lock:
            if self._size < self._max_size:
                self._size += 1
                return await self._create_connection()

        # Pool exhausted — wait for a connection to be released
        conn = await self._pool.get()
        try:
            await conn.execute("SELECT 1")
            return conn
        except Exception:
            async with self._lock:
                self._size -= 1
            return await self._create_connection()

    async def release(self, conn: aiosqlite.Connection):
        try:
            self._pool.put_nowait(conn)
        except asyncio.QueueFull:
            await conn.close()
            async with self._lock:
                self._size -= 1

    async def close_all(self):
        while not self._pool.empty():
            conn = self._pool.get_nowait()
            await conn.close()
        async with self._lock:
            self._size = 0


# Global pool instance (initialized lazily)
_pool: Optional[ConnectionPool] = None


def _get_pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        _pool = ConnectionPool(config.DATABASE_PATH, max_size=5)
    return _pool


async def reset_pool():
    """Close all pooled connections and force new ones on next request.

    Must be called after the database file is replaced (e.g. import)
    so that stale connections to the old file are discarded.
    """
    global _pool
    if _pool is not None:
        await _pool.close_all()
        _pool = None

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
    max_material_life REAL DEFAULT 0.375,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE,
    UNIQUE(job_id, set_no, cam_no)
);

CREATE INDEX IF NOT EXISTS idx_cam_items_job ON cam_items(job_id);
CREATE INDEX IF NOT EXISTS idx_cam_items_station ON cam_items(status_station);
CREATE INDEX IF NOT EXISTS idx_cam_items_updated ON cam_items(status_updated_at);
CREATE INDEX IF NOT EXISTS idx_cam_items_die_position ON cam_items(die_position);
CREATE INDEX IF NOT EXISTS idx_cam_items_set_cam ON cam_items(job_id, set_no, cam_no);
CREATE INDEX IF NOT EXISTS idx_cam_items_auto_bump ON cam_items(job_id, die_position, status_station);

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

-- Users table: operators and admins
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    pin_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'user' CHECK(role IN ('user', 'admin')),
    active BOOLEAN NOT NULL DEFAULT 1,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_users_username ON users(username);
CREATE INDEX IF NOT EXISTS idx_users_active ON users(active);

-- Tool lifespans table: tracks each lifespan (creation/refill to refill)
CREATE TABLE IF NOT EXISTS tool_lifespans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cam_item_id INTEGER NOT NULL,
    lifespan_number INTEGER NOT NULL DEFAULT 1,
    started_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    ended_at TIMESTAMP,
    total_material_removed REAL NOT NULL DEFAULT 0.0,
    sharpen_count INTEGER NOT NULL DEFAULT 0,
    max_material_life REAL NOT NULL DEFAULT 0.375,
    FOREIGN KEY (cam_item_id) REFERENCES cam_items(id) ON DELETE CASCADE,
    UNIQUE(cam_item_id, lifespan_number)
);

CREATE INDEX IF NOT EXISTS idx_tool_lifespans_cam_item ON tool_lifespans(cam_item_id);
CREATE INDEX IF NOT EXISTS idx_tool_lifespans_active ON tool_lifespans(cam_item_id, ended_at);

-- Sessions table: persistent session storage (survives restarts)
CREATE TABLE IF NOT EXISTS sessions (
    token TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    username TEXT NOT NULL,
    display_name TEXT NOT NULL,
    role TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_sessions_expires ON sessions(expires_at);

-- Priority changes audit log: tracks every priority change with reason
CREATE TABLE IF NOT EXISTS priority_changes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL,
    changed_by_user_id INTEGER NOT NULL,
    changed_by_username TEXT NOT NULL,
    old_priority TEXT NOT NULL,
    new_priority TEXT NOT NULL,
    reason TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'all_jobs' CHECK(source IN ('all_jobs', 'top5')),
    changed_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE,
    FOREIGN KEY (changed_by_user_id) REFERENCES users(id)
);

CREATE INDEX IF NOT EXISTS idx_priority_changes_job ON priority_changes(job_id);
CREATE INDEX IF NOT EXISTS idx_priority_changes_time ON priority_changes(changed_at DESC);
CREATE INDEX IF NOT EXISTS idx_priority_changes_user ON priority_changes(changed_by_user_id);

-- Insert default config values
INSERT OR IGNORE INTO config (key, value) VALUES ('auto_bump_enabled', 'true');
INSERT OR IGNORE INTO config (key, value) VALUES ('default_operator', 'kiosk');
INSERT OR IGNORE INTO config (key, value) VALUES ('default_material_life', '0.375');
"""

def init_database(db_path: str = None):
    """Initialize the database with schema (synchronous for setup scripts)"""
    if db_path is None:
        db_path = config.DATABASE_PATH

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.executescript(SCHEMA_SQL)
    conn.commit()
    ensure_default_admin(conn)
    conn.close()
    logger.info(f"Database initialized at {db_path}")

async def get_db():
    """Get async database connection from pool (for FastAPI dependency injection)"""
    pool = _get_pool()
    conn = await pool.acquire()
    try:
        yield conn
    finally:
        await pool.release(conn)

@asynccontextmanager
async def get_db_connection():
    """Get async database connection (context manager for manual use)"""
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA foreign_keys = ON")
        yield db

async def get_config_value(key: str, default: str = None, db=None) -> Optional[str]:
    """Get configuration value from database.

    If a db connection is provided, uses it directly. Otherwise opens a new connection.
    """
    if db is not None:
        cursor = await db.execute("SELECT value FROM config WHERE key = ?", (key,))
        row = await cursor.fetchone()
        return row['value'] if row else default
    async with get_db_connection() as conn:
        cursor = await conn.execute("SELECT value FROM config WHERE key = ?", (key,))
        row = await cursor.fetchone()
        return row['value'] if row else default

async def set_config_value(key: str, value: str, db=None):
    """Set configuration value in database.

    If a db connection is provided, uses it directly (caller must commit).
    Otherwise opens a new connection and auto-commits.
    """
    if db is not None:
        await db.execute(
            "INSERT OR REPLACE INTO config (key, value, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP)",
            (key, value)
        )
        return
    async with get_db_connection() as conn:
        await conn.execute(
            "INSERT OR REPLACE INTO config (key, value, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP)",
            (key, value)
        )
        await conn.commit()

def hash_pin(pin: str) -> str:
    """Hash a PIN with a random salt using PBKDF2"""
    salt = secrets.token_hex(16)
    hash_val = hashlib.pbkdf2_hmac('sha256', pin.encode(), salt.encode(), 10000)
    return f"{salt}:{hash_val.hex()}"


def verify_pin(pin: str, pin_hash: str) -> bool:
    """Verify a PIN against a stored hash"""
    try:
        salt, hash_val = pin_hash.split(':')
        new_hash = hashlib.pbkdf2_hmac('sha256', pin.encode(), salt.encode(), 10000)
        return secrets.compare_digest(new_hash.hex(), hash_val)
    except Exception:
        return False


def ensure_default_admin(conn):
    """Create default admin user if no users exist"""
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM users")
    count = cursor.fetchone()[0]
    if count == 0:
        default_pin = "1234"
        pin_hash = hash_pin(default_pin)
        cursor.execute(
            "INSERT INTO users (username, display_name, pin_hash, role) VALUES (?, ?, ?, ?)",
            ("admin", "Administrator", pin_hash, "admin")
        )
        conn.commit()
        logger.info("Created default admin user (username: admin, PIN: 1234) - CHANGE THIS!")


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

    # Check if users table exists
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='users'")
    if not cursor.fetchone():
        logger.info("  Creating users table...")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                display_name TEXT NOT NULL,
                pin_hash TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'user' CHECK(role IN ('user', 'admin')),
                active BOOLEAN NOT NULL DEFAULT 1,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_users_username ON users(username)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_users_active ON users(active)")
        logger.info("    Created users table")

    # Check if max_material_life column exists on cam_items
    cursor.execute("PRAGMA table_info(cam_items)")
    columns = [col[1] for col in cursor.fetchall()]

    if 'max_material_life' not in columns:
        logger.info("  Adding max_material_life column to cam_items...")
        cursor.execute("ALTER TABLE cam_items ADD COLUMN max_material_life REAL DEFAULT 0.375")
        logger.info("    Added max_material_life column (defaults to 0.375)")

    # Check if tool_lifespans table exists
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='tool_lifespans'")
    lifespans_existed = cursor.fetchone() is not None

    if not lifespans_existed:
        logger.info("  Creating tool_lifespans table...")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS tool_lifespans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cam_item_id INTEGER NOT NULL,
                lifespan_number INTEGER NOT NULL DEFAULT 1,
                started_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                ended_at TIMESTAMP,
                total_material_removed REAL NOT NULL DEFAULT 0.0,
                sharpen_count INTEGER NOT NULL DEFAULT 0,
                max_material_life REAL NOT NULL DEFAULT 0.375,
                FOREIGN KEY (cam_item_id) REFERENCES cam_items(id) ON DELETE CASCADE,
                UNIQUE(cam_item_id, lifespan_number)
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_tool_lifespans_cam_item ON tool_lifespans(cam_item_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_tool_lifespans_active ON tool_lifespans(cam_item_id, ended_at)")
        logger.info("    Created tool_lifespans table")

    # Insert default_material_life config if missing
    cursor.execute("INSERT OR IGNORE INTO config (key, value) VALUES ('default_material_life', '0.375')")

    # Ensure composite indices exist (idempotent — safe to re-run)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_cam_items_set_cam ON cam_items(job_id, set_no, cam_no)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_cam_items_auto_bump ON cam_items(job_id, die_position, status_station)")

    # Check if sessions table exists
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='sessions'")
    if not cursor.fetchone():
        logger.info("  Creating sessions table...")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                token TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                username TEXT NOT NULL,
                display_name TEXT NOT NULL,
                role TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_sessions_expires ON sessions(expires_at)")
        logger.info("    Created sessions table")

    # Backfill lifespan data for existing cam_items if table was just created
    if not lifespans_existed:
        logger.info("  Backfilling lifespan data from existing moves...")
        cursor.execute("SELECT id FROM cam_items")
        cam_ids = [row[0] for row in cursor.fetchall()]

        backfill_count = 0
        for cam_id in cam_ids:
            cursor.execute("""
                SELECT id, from_station, to_station, moved_at, material_removed
                FROM moves
                WHERE cam_item_id = ? AND undone = 0
                ORDER BY moved_at ASC, id ASC
            """, (cam_id,))
            moves = cursor.fetchall()

            lifespan_number = 0
            current_start = None
            current_total = 0.0
            current_count = 0

            for move in moves:
                m_id, from_st, to_st, moved_at, mat_removed = move

                # Start new lifespan on creation or return from refill
                if from_st == 'new' or (from_st == 'refill' and to_st != 'refill'):
                    if current_start is not None and lifespan_number > 0:
                        # Close previous open lifespan (shouldn't happen normally, but safety)
                        pass
                    lifespan_number += 1
                    current_start = moved_at
                    current_total = 0.0
                    current_count = 0

                # Accumulate sharpening data
                if from_st == 'sharpen' and to_st == 'cabinet' and mat_removed is not None:
                    current_total += mat_removed
                    current_count += 1

                # Close lifespan on refill
                if to_st == 'refill' and current_start is not None:
                    cursor.execute("""
                        INSERT INTO tool_lifespans
                        (cam_item_id, lifespan_number, started_at, ended_at,
                         total_material_removed, sharpen_count, max_material_life)
                        VALUES (?, ?, ?, ?, ?, ?, 0.375)
                    """, (cam_id, lifespan_number, current_start, moved_at,
                          current_total, current_count))
                    backfill_count += 1
                    current_start = None
                    current_total = 0.0
                    current_count = 0

            # If lifespan is still open, insert as active (no ended_at)
            if current_start is not None:
                if lifespan_number == 0:
                    lifespan_number = 1
                cursor.execute("""
                    INSERT INTO tool_lifespans
                    (cam_item_id, lifespan_number, started_at, ended_at,
                     total_material_removed, sharpen_count, max_material_life)
                    VALUES (?, ?, ?, NULL, ?, ?, 0.375)
                """, (cam_id, lifespan_number, current_start,
                      current_total, current_count))
                backfill_count += 1

        logger.info(f"    Backfilled {backfill_count} lifespan records for {len(cam_ids)} cam items")

    # Check if priority_changes table exists
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='priority_changes'")
    if not cursor.fetchone():
        logger.info("  Creating priority_changes table...")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS priority_changes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id INTEGER NOT NULL,
                changed_by_user_id INTEGER NOT NULL,
                changed_by_username TEXT NOT NULL,
                old_priority TEXT NOT NULL,
                new_priority TEXT NOT NULL,
                reason TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT 'all_jobs' CHECK(source IN ('all_jobs', 'top5')),
                changed_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE,
                FOREIGN KEY (changed_by_user_id) REFERENCES users(id)
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_priority_changes_job ON priority_changes(job_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_priority_changes_time ON priority_changes(changed_at DESC)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_priority_changes_user ON priority_changes(changed_by_user_id)")
        logger.info("    Created priority_changes table")

    conn.commit()

    # Ensure default admin exists
    ensure_default_admin(conn)

    conn.close()
    logger.info("✓ Database migration complete")

if __name__ == "__main__":
    # Allow running this script directly to initialize database
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == 'migrate':
        migrate_database()
    else:
        init_database()
