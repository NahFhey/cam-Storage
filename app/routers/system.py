"""Health, runtime configuration, CSV exports and database backup/restore."""
import logging
import os
import shutil
import sqlite3
import tempfile
from datetime import datetime

import aiosqlite
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse

import config
from app.cache import cache
from app.deps import get_admin_user, get_current_user
from app.schemas import ConfigUpdate
from app.utils import csv_response, fetch_one
from database import get_config_value, get_db, migrate_database, reset_pool, set_config_value

logger = logging.getLogger(__name__)
router = APIRouter()

APP_VERSION = "2.0.0"
MAX_IMPORT_BYTES = 100 * 1024 * 1024
REQUIRED_TABLES = {'jobs', 'cam_items', 'moves'}


@router.get("/health")
async def health_check(db: aiosqlite.Connection = Depends(get_db)):
    """Service status, database connectivity and disk space."""
    try:
        disk = shutil.disk_usage(os.path.dirname(os.path.abspath(config.DATABASE_PATH)))
        counts = await fetch_one(db, """
            SELECT (SELECT COUNT(*) FROM jobs) as jobs_count,
                   (SELECT COUNT(*) FROM cam_items) as cams_count,
                   (SELECT COUNT(*) FROM moves WHERE undone = 0) as moves_count""")
        gb = 1024 ** 3
        return {
            "status": "healthy",
            "timestamp": datetime.now().isoformat(),
            "database": {"status": "connected", **counts},
            "disk": {
                "free_gb": round(disk.free / gb, 2),
                "total_gb": round(disk.total / gb, 2),
                "used_percent": round((1 - disk.free / disk.total) * 100, 1)
            },
            "version": APP_VERSION
        }
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        return JSONResponse({"status": "unhealthy"}, status_code=503)


# ========== Configuration ==========

async def _read_config(db) -> dict:
    auto_bump = await get_config_value('auto_bump_enabled', 'false', db=db)
    default_life = await get_config_value('default_material_life', str(config.DEFAULT_MATERIAL_LIFE), db=db)
    return {
        "auto_bump_enabled": auto_bump.lower() == 'true',
        "default_material_life": float(default_life)
    }


@router.get("/api/config")
async def get_config(db: aiosqlite.Connection = Depends(get_db)):
    return await _read_config(db)


@router.patch("/api/config")
async def update_config(config_update: ConfigUpdate, db: aiosqlite.Connection = Depends(get_db),
                        admin_user: dict = Depends(get_admin_user)):
    if config_update.auto_bump_enabled is not None:
        await set_config_value('auto_bump_enabled', 'true' if config_update.auto_bump_enabled else 'false', db=db)
    if config_update.default_material_life is not None:
        await set_config_value('default_material_life', str(config_update.default_material_life), db=db)
    await db.commit()
    return await _read_config(db)


# ========== CSV Exports ==========

@router.get("/api/export/jobs/csv")
async def export_jobs_csv(user: dict = Depends(get_current_user)):
    columns = ['id', 's_number', 'title', 'priority_level', 'created_at', 'notes']
    return csv_response(
        "jobs.csv", columns,
        f"SELECT {', '.join(columns)} FROM jobs ORDER BY s_number",
        lambda r: [r[c] for c in columns]
    )


@router.get("/api/export/cam-items/csv")
async def export_cam_items_csv(user: dict = Depends(get_current_user)):
    columns = ['s_number', 'set_no', 'cam_no', 'die_position', 'status_station', 'status_updated_at',
               'enter_die_steel', 'exit_die_steel', 'max_material_life', 'eol_cycles_expected',
               'created_at', 'notes']
    return csv_response(
        "cam_items.csv", columns,
        """SELECT j.s_number, c.set_no, c.cam_no, c.die_position, c.status_station, c.status_updated_at,
                  c.enter_die_steel, c.exit_die_steel, c.max_material_life, c.eol_cycles_expected,
                  c.created_at, c.notes
           FROM cam_items c JOIN jobs j ON c.job_id = j.id
           ORDER BY j.s_number, c.set_no, c.cam_no""",
        lambda r: [r[c] for c in columns]
    )


@router.get("/api/export/moves/csv")
async def export_moves_csv(user: dict = Depends(get_current_user)):
    columns = ['s_number', 'set_no', 'cam_no', 'from_station', 'to_station',
               'moved_at', 'operator', 'material_removed', 'notes', 'undone']
    return csv_response(
        "moves.csv", columns,
        """SELECT j.s_number, c.set_no, c.cam_no, m.from_station, m.to_station,
                  m.moved_at, m.operator, m.material_removed, m.notes, m.undone
           FROM moves m
           JOIN cam_items c ON m.cam_item_id = c.id
           JOIN jobs j ON c.job_id = j.id
           ORDER BY m.moved_at DESC, m.id DESC""",
        lambda r: [r[c] for c in columns]
    )


@router.get("/api/export/tool-lifespans/csv")
async def export_tool_lifespans_csv(user: dict = Depends(get_current_user)):
    columns = ['s_number', 'set_no', 'cam_no', 'lifespan_number', 'started_at', 'ended_at',
               'sharpen_count', 'total_material_removed', 'max_material_life', 'percent_used', 'status']
    return csv_response(
        "tool_lifespans.csv", columns,
        """SELECT j.s_number, c.set_no, c.cam_no, tl.lifespan_number, tl.started_at, tl.ended_at,
                  tl.sharpen_count, tl.total_material_removed, tl.max_material_life,
                  CASE WHEN tl.max_material_life > 0
                       THEN ROUND((tl.total_material_removed / tl.max_material_life) * 100, 1)
                       ELSE 0 END as percent_used,
                  CASE WHEN tl.ended_at IS NULL THEN 'active' ELSE 'completed' END as status
           FROM tool_lifespans tl
           JOIN cam_items c ON tl.cam_item_id = c.id
           JOIN jobs j ON c.job_id = j.id
           ORDER BY j.s_number, c.set_no, c.cam_no, tl.lifespan_number""",
        lambda r: [r[c] for c in columns]
    )


@router.get("/api/export/tool-summary/csv")
async def export_tool_summary_csv(user: dict = Depends(get_current_user)):
    """One row per tool with aggregates across all of its lifespans."""
    columns = ['s_number', 'set_no', 'cam_no', 'current_station', 'current_lifespan_number',
               'total_refills', 'total_sharpenings', 'total_material_removed',
               'current_cycle_sharpenings', 'current_cycle_material_removed',
               'current_cycle_percent_used', 'max_material_life']
    return csv_response(
        "tool_summary.csv", columns,
        """SELECT j.s_number, c.set_no, c.cam_no,
                  c.status_station AS current_station,
                  c.max_material_life,
                  COALESCE(cur.lifespan_number, MAX(tl.lifespan_number), 0) AS current_lifespan_number,
                  MAX(COALESCE(MAX(tl.lifespan_number), 1) - 1, 0) AS total_refills,
                  COALESCE(SUM(tl.sharpen_count), 0) AS total_sharpenings,
                  COALESCE(SUM(tl.total_material_removed), 0.0) AS total_material_removed,
                  COALESCE(cur.sharpen_count, 0) AS current_cycle_sharpenings,
                  COALESCE(cur.total_material_removed, 0.0) AS current_cycle_material_removed,
                  CASE WHEN c.max_material_life > 0
                       THEN ROUND(COALESCE(cur.total_material_removed, 0.0) / c.max_material_life * 100, 1)
                       ELSE 0 END AS current_cycle_percent_used
           FROM cam_items c
           JOIN jobs j ON c.job_id = j.id
           LEFT JOIN tool_lifespans tl ON tl.cam_item_id = c.id
           LEFT JOIN tool_lifespans cur ON cur.cam_item_id = c.id AND cur.ended_at IS NULL
           GROUP BY c.id
           ORDER BY j.s_number, c.set_no, c.cam_no""",
        lambda r: [r[c] for c in columns]
    )


@router.get("/api/export/priority-changes/csv")
async def export_priority_changes_csv(admin_user: dict = Depends(get_admin_user)):
    columns = ['s_number', 'title', 'changed_by', 'old_priority', 'new_priority', 'reason', 'source', 'changed_at']
    return csv_response(
        "priority_changes.csv", columns,
        """SELECT j.s_number, j.title, pc.changed_by_username AS changed_by, pc.old_priority,
                  pc.new_priority, pc.reason, pc.source, pc.changed_at
           FROM priority_changes pc
           JOIN jobs j ON pc.job_id = j.id
           ORDER BY pc.changed_at DESC, pc.id DESC""",
        lambda r: [r[c] for c in columns]
    )


# ========== Database Backup / Restore ==========

@router.get("/api/export/database")
async def export_database(admin_user: dict = Depends(get_admin_user)):
    """Download the SQLite database file."""
    return FileResponse(config.DATABASE_PATH, media_type="application/octet-stream",
                        filename="cam_tracking_backup.db")


def _inspect_database(path: str) -> dict:
    """Validate an uploaded file is a CAM tracking database and count its rows."""
    try:
        conn = sqlite3.connect(path)
        try:
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            missing = REQUIRED_TABLES - tables
            if missing:
                raise HTTPException(status_code=400,
                                    detail=f"Invalid database: missing required tables: {', '.join(sorted(missing))}")
            return {f"{t}_count": conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in REQUIRED_TABLES}
        finally:
            conn.close()
    except sqlite3.DatabaseError as e:
        raise HTTPException(status_code=400, detail=f"Invalid SQLite database file: {e}")


@router.post("/api/import/database")
async def import_database(file: UploadFile = File(...), admin_user: dict = Depends(get_admin_user)):
    """Replace ALL data with an uploaded SQLite database (the current file is backed up first)."""
    if not file.filename.endswith(('.db', '.sqlite', '.sqlite3')):
        raise HTTPException(status_code=400,
                            detail="Invalid file type. Must be a SQLite database file (.db, .sqlite, or .sqlite3)")

    content = await file.read()
    size_mb = len(content) / (1024 * 1024)
    if len(content) > MAX_IMPORT_BYTES:
        raise HTTPException(status_code=413,
                            detail=f"File too large. Maximum size is 100MB, uploaded file is {size_mb:.2f}MB")
    if not content:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")

    logger.info(f"Admin '{admin_user['username']}' uploading database file: {file.filename} ({size_mb:.2f}MB)")

    fd, temp_path = tempfile.mkstemp(suffix='.db')
    try:
        with os.fdopen(fd, 'wb') as temp_file:
            temp_file.write(content)

        counts = _inspect_database(temp_path)

        backup_path = f"{config.DATABASE_PATH}.backup.{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        if os.path.exists(config.DATABASE_PATH):
            shutil.copy2(config.DATABASE_PATH, backup_path)

        # Open connections to the old file are dropped; the frontend reloads afterwards
        logger.warning(f"Admin '{admin_user['username']}' replacing database file")
        shutil.move(temp_path, config.DATABASE_PATH)
        await reset_pool()
        # Older backups may predate newer tables (e.g. priority_changes)
        migrate_database()
        cache.invalidate()
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Database import failed for admin '{admin_user['username']}': {e}")
        raise HTTPException(status_code=500, detail=f"Import failed: {e}")
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)

    logger.info(f"Database imported by admin '{admin_user['username']}': {counts}")
    return {
        "message": "Database imported successfully",
        "backup_created": backup_path if os.path.exists(backup_path) else None,
        **counts
    }
