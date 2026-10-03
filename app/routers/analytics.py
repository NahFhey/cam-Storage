"""Search and read-only analytics."""
import logging

import aiosqlite
from fastapi import APIRouter, Depends, Request

from app.cache import cache
from app.limiter import limiter
from app.utils import JOB_COLUMNS, fetch_all, fetch_one
from business_logic import get_tool_stats
from database import get_db
from entry_parser import resolve_entry

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api")

# Rows for moves within the last N days that were not undone
RECENT_MOVES = "undone = 0 AND moved_at >= datetime('now', '-' || ? || ' days')"


@router.get("/search")
@limiter.limit("100/minute")
async def search(request: Request, q: str, db: aiosqlite.Connection = Depends(get_db)):
    """Search jobs by S-number/title and tools by any entry format (e.g. 1793-1-2)."""
    q_stripped = q.upper().strip().lstrip('S')
    jobs = await fetch_all(
        db,
        f"""SELECT {JOB_COLUMNS} FROM jobs
            WHERE s_number LIKE ? OR s_number LIKE ? OR title LIKE ?
            ORDER BY s_number""",
        (f"%{q}%", f"%{q_stripped}%", f"%{q}%")
    )

    cam_items = []
    try:
        result = await resolve_entry(db, q)
        if result['status'] == 'exact':
            cam_items = [result['cam_item']]
        elif result['status'] == 'multiple':
            cam_items = result['candidates']
        # Tools all belong to the resolved job; include its S-number for display
        for cam in cam_items:
            cam['s_number'] = result['job']['s_number']
    except Exception as e:
        logger.debug(f"Entry parsing failed for search query '{q}': {e}")

    return {"query": q, "jobs": jobs, "cam_items": cam_items}


@router.get("/analytics/station-counts")
async def analytics_station_counts(db: aiosqlite.Connection = Depends(get_db)):
    """Tool counts by station (cached for 30s)."""
    counts = cache.get("station_counts")
    if counts is None:
        counts = await fetch_all(db, """
            SELECT status_station as station, COUNT(*) as count
            FROM cam_items
            GROUP BY status_station
            ORDER BY CASE status_station
                WHEN 'active' THEN 1 WHEN 'sharpen' THEN 2 WHEN 'cabinet' THEN 3 WHEN 'refill' THEN 4
            END""")
        cache.set("station_counts", counts)
    return counts


@router.get("/analytics/moves-recent")
async def analytics_moves_recent(days: int = 7, db: aiosqlite.Connection = Depends(get_db)):
    """Move counts per day."""
    return await fetch_all(db, f"""
        SELECT DATE(moved_at) as date, COUNT(*) as count
        FROM moves WHERE {RECENT_MOVES}
        GROUP BY DATE(moved_at) ORDER BY date DESC""", (days,))


@router.get("/analytics/dwell-times")
async def analytics_dwell_times(db: aiosqlite.Connection = Depends(get_db)):
    """Average hours tools have sat at their current station."""
    return await fetch_all(db, """
        SELECT status_station as station,
               AVG((julianday('now') - julianday(status_updated_at)) * 24) as avg_hours,
               COUNT(*) as count
        FROM cam_items
        GROUP BY status_station""")


@router.get("/analytics/cycle-counts")
async def analytics_cycle_counts(limit: int = 20, db: aiosqlite.Connection = Depends(get_db)):
    """Cycle counts (moves into 'active') per cam item."""
    return await fetch_all(db, """
        SELECT c.id as cam_item_id, j.s_number, c.set_no, c.cam_no, COUNT(m.id) as cycle_count
        FROM cam_items c
        JOIN jobs j ON c.job_id = j.id
        LEFT JOIN moves m ON c.id = m.cam_item_id AND m.to_station = 'active' AND m.undone = 0
        GROUP BY c.id
        ORDER BY cycle_count DESC
        LIMIT ?""", (limit,))


@router.get("/analytics/sharpen-backlog")
async def analytics_sharpen_backlog(db: aiosqlite.Connection = Depends(get_db)):
    return await fetch_one(db, """
        SELECT COUNT(*) as backlog_size,
               AVG((julianday('now') - julianday(status_updated_at)) * 24) as avg_hours_in_sharpen
        FROM cam_items WHERE status_station = 'sharpen'""")


@router.get("/analytics/material-stats")
async def analytics_material_stats(limit: int = 20, db: aiosqlite.Connection = Depends(get_db)):
    """Most-sharpened tools with last/average material removed."""
    return await get_tool_stats(db, only_sharpened=True, limit=min(limit, 500))


@router.get("/analytics/refill-forecast")
async def analytics_refill_forecast(limit: int = 50, db: aiosqlite.Connection = Depends(get_db)):
    """Tools approaching refill, sorted by percent of material life used."""
    return await fetch_all(db, """
        SELECT tl.cam_item_id, tl.total_material_removed, tl.sharpen_count, tl.max_material_life,
               tl.started_at, c.set_no, c.cam_no, c.status_station, c.job_id, j.s_number, j.title,
               ROUND((tl.total_material_removed / tl.max_material_life) * 100, 1) as percent_used,
               ROUND(tl.max_material_life - tl.total_material_removed, 3) as material_remaining
        FROM tool_lifespans tl
        JOIN cam_items c ON tl.cam_item_id = c.id
        JOIN jobs j ON c.job_id = j.id
        WHERE tl.ended_at IS NULL AND tl.max_material_life > 0 AND tl.sharpen_count > 0
        ORDER BY percent_used DESC
        LIMIT ?""", (limit,))


@router.get("/analytics/station-transitions")
async def analytics_station_transitions(days: int = 30, db: aiosqlite.Connection = Depends(get_db)):
    return await fetch_all(db, f"""
        SELECT from_station, to_station, COUNT(*) as count
        FROM moves WHERE {RECENT_MOVES}
        GROUP BY from_station, to_station ORDER BY count DESC""", (days,))


@router.get("/analytics/operator-activity")
async def analytics_operator_activity(days: int = 30, db: aiosqlite.Connection = Depends(get_db)):
    return await fetch_all(db, f"""
        SELECT COALESCE(operator, 'Unknown') as operator,
               COUNT(*) as move_count,
               COALESCE(SUM(material_removed), 0) as total_material_removed,
               COUNT(CASE WHEN material_removed IS NOT NULL THEN 1 END) as sharpen_moves
        FROM moves WHERE {RECENT_MOVES}
        GROUP BY operator ORDER BY move_count DESC""", (days,))


@router.get("/analytics/lifespan-stats")
async def analytics_lifespan_stats(db: aiosqlite.Connection = Depends(get_db)):
    """Averages for completed and in-progress tool lifespans."""
    completed = await fetch_one(db, """
        SELECT COUNT(*) as completed_lifespans,
               AVG(sharpen_count) as avg_sharpenings,
               AVG(total_material_removed) as avg_material_removed,
               AVG((julianday(ended_at) - julianday(started_at)) * 24) as avg_lifespan_hours,
               MIN(sharpen_count) as min_sharpenings,
               MAX(sharpen_count) as max_sharpenings
        FROM tool_lifespans WHERE ended_at IS NOT NULL""")
    active = await fetch_one(db, """
        SELECT COUNT(*) as active_lifespans,
               AVG(sharpen_count) as avg_sharpenings,
               AVG(total_material_removed) as avg_material_removed,
               AVG(CASE WHEN max_material_life > 0
                   THEN (total_material_removed / max_material_life) * 100 ELSE 0 END) as avg_percent_used
        FROM tool_lifespans WHERE ended_at IS NULL""")
    return {"completed": completed, "active": active}


@router.get("/analytics/material-trends")
async def analytics_material_trends(days: int = 30, db: aiosqlite.Connection = Depends(get_db)):
    """Daily move counts and material removed."""
    return await fetch_all(db, f"""
        SELECT DATE(moved_at) as date,
               COUNT(*) as move_count,
               COALESCE(SUM(material_removed), 0) as total_material_removed,
               COUNT(CASE WHEN material_removed IS NOT NULL THEN 1 END) as sharpen_count
        FROM moves WHERE {RECENT_MOVES}
        GROUP BY DATE(moved_at) ORDER BY date""", (days,))
