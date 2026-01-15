"""
FastAPI backend for CAM Tracking Kiosk
"""
from fastapi import FastAPI, HTTPException, Depends, Response
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime, timedelta
import aiosqlite
import csv
import io
import shutil

import config
from database import get_db, get_config_value, set_config_value, init_database
from entry_parser import parse_manual_entry, resolve_entry
from business_logic import move_cam_to_station, undo_last_move, generate_hot_list

# Initialize FastAPI app
app = FastAPI(title="CAM Tracking Kiosk API", version="1.0.0")

# Serve static files (frontend)
app.mount("/static", StaticFiles(directory="static"), name="static")

# Pydantic models for request/response
class JobCreate(BaseModel):
    s_number: str
    title: Optional[str] = None
    priority_level: str = "low"
    notes: Optional[str] = None

class JobUpdate(BaseModel):
    title: Optional[str] = None
    priority_level: Optional[str] = None
    notes: Optional[str] = None

class CamItemCreate(BaseModel):
    job_id: int
    set_no: int
    cam_no: int
    die_position: Optional[str] = None
    enter_die_steel: Optional[str] = None
    exit_die_steel: Optional[str] = None
    status_station: str = "cabinet"
    notes: Optional[str] = None
    eol_cycles_expected: Optional[int] = None

class CamItemUpdate(BaseModel):
    die_position: Optional[str] = None
    enter_die_steel: Optional[str] = None
    exit_die_steel: Optional[str] = None
    notes: Optional[str] = None
    eol_cycles_expected: Optional[int] = None

class CamItemBulkCreate(BaseModel):
    job_id: int
    sets: List[int]  # e.g., [1, 2, 3]
    cams_per_set: int  # e.g., 4
    initial_station: str = "cabinet"

class MoveRequest(BaseModel):
    cam_item_id: int
    to_station: str
    operator: Optional[str] = None
    notes: Optional[str] = None
    auto_bump: Optional[bool] = None

class EntryResolveRequest(BaseModel):
    entry: str

class ConfigUpdate(BaseModel):
    auto_bump_enabled: Optional[bool] = None

# Root endpoint - serve main page
@app.get("/")
async def root():
    return FileResponse("static/index.html")

# ========== Jobs Endpoints ==========

@app.get("/api/jobs")
async def list_jobs(db: aiosqlite.Connection = Depends(get_db)):
    """List all jobs"""
    cursor = await db.execute(
        "SELECT * FROM jobs ORDER BY created_at DESC"
    )
    jobs = await cursor.fetchall()
    return [dict(job) for job in jobs]

@app.get("/api/jobs/{job_id}")
async def get_job(job_id: int, db: aiosqlite.Connection = Depends(get_db)):
    """Get a specific job with its cam items"""
    cursor = await db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
    job = await cursor.fetchone()

    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    # Get cam items for this job
    cursor = await db.execute(
        """
        SELECT * FROM cam_items
        WHERE job_id = ?
        ORDER BY set_no, cam_no
        """,
        (job_id,)
    )
    cam_items = await cursor.fetchall()

    return {
        "job": dict(job),
        "cam_items": [dict(c) for c in cam_items]
    }

@app.post("/api/jobs")
async def create_job(job: JobCreate, db: aiosqlite.Connection = Depends(get_db)):
    """Create a new job"""
    # Validate priority level
    if job.priority_level not in config.PRIORITY_LEVELS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid priority level. Must be one of: {', '.join(config.PRIORITY_LEVELS)}"
        )

    try:
        cursor = await db.execute(
            """
            INSERT INTO jobs (s_number, title, priority_level, notes)
            VALUES (?, ?, ?, ?)
            """,
            (job.s_number, job.title, job.priority_level, job.notes)
        )
        await db.commit()
        job_id = cursor.lastrowid

        cursor = await db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
        new_job = await cursor.fetchone()
        return dict(new_job)
    except aiosqlite.IntegrityError:
        raise HTTPException(status_code=400, detail=f"Job {job.s_number} already exists")

@app.patch("/api/jobs/{job_id}")
async def update_job(job_id: int, job: JobUpdate, db: aiosqlite.Connection = Depends(get_db)):
    """Update a job"""
    updates = []
    values = []

    if job.title is not None:
        updates.append("title = ?")
        values.append(job.title)
    if job.priority_level is not None:
        if job.priority_level not in config.PRIORITY_LEVELS:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid priority level. Must be one of: {', '.join(config.PRIORITY_LEVELS)}"
            )
        updates.append("priority_level = ?")
        values.append(job.priority_level)
    if job.notes is not None:
        updates.append("notes = ?")
        values.append(job.notes)

    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    values.append(job_id)
    await db.execute(
        f"UPDATE jobs SET {', '.join(updates)} WHERE id = ?",
        values
    )
    await db.commit()

    cursor = await db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
    updated_job = await cursor.fetchone()
    return dict(updated_job)

@app.delete("/api/jobs/{job_id}")
async def delete_job(job_id: int, db: aiosqlite.Connection = Depends(get_db)):
    """Delete a job (cascades to cam items and moves)"""
    await db.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
    await db.commit()
    return {"success": True, "message": "Job deleted"}

# ========== CAM Items Endpoints ==========

@app.get("/api/cam-items")
async def list_cam_items(
    job_id: Optional[int] = None,
    station: Optional[str] = None,
    db: aiosqlite.Connection = Depends(get_db)
):
    """List cam items with optional filters"""
    query = "SELECT * FROM cam_items WHERE 1=1"
    params = []

    if job_id:
        query += " AND job_id = ?"
        params.append(job_id)

    if station:
        query += " AND status_station = ?"
        params.append(station)

    query += " ORDER BY status_updated_at DESC"

    cursor = await db.execute(query, params)
    items = await cursor.fetchall()
    return [dict(item) for item in items]

@app.get("/api/cam-items/{cam_item_id}")
async def get_cam_item(cam_item_id: int, db: aiosqlite.Connection = Depends(get_db)):
    """Get a specific cam item with its move history"""
    cursor = await db.execute("SELECT * FROM cam_items WHERE id = ?", (cam_item_id,))
    cam_item = await cursor.fetchone()

    if not cam_item:
        raise HTTPException(status_code=404, detail="CAM item not found")

    # Get move history
    cursor = await db.execute(
        """
        SELECT * FROM moves
        WHERE cam_item_id = ?
        ORDER BY moved_at DESC
        """,
        (cam_item_id,)
    )
    moves = await cursor.fetchall()

    # Get job info
    cursor = await db.execute("SELECT * FROM jobs WHERE id = ?", (cam_item['job_id'],))
    job = await cursor.fetchone()

    return {
        "cam_item": dict(cam_item),
        "job": dict(job) if job else None,
        "moves": [dict(m) for m in moves]
    }

@app.post("/api/cam-items")
async def create_cam_item(item: CamItemCreate, db: aiosqlite.Connection = Depends(get_db)):
    """Create a single cam item"""
    if item.status_station not in config.STATIONS:
        raise HTTPException(status_code=400, detail=f"Invalid station: {item.status_station}")

    if item.die_position and item.die_position not in config.DIE_POSITIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid die position. Must be one of: {', '.join(config.DIE_POSITIONS)}"
        )

    try:
        cursor = await db.execute(
            """
            INSERT INTO cam_items
            (job_id, set_no, cam_no, die_position, enter_die_steel, exit_die_steel,
             status_station, notes, eol_cycles_expected)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (item.job_id, item.set_no, item.cam_no, item.die_position, item.enter_die_steel,
             item.exit_die_steel, item.status_station, item.notes, item.eol_cycles_expected)
        )
        await db.commit()
        cam_item_id = cursor.lastrowid

        # Record initial move
        await db.execute(
            """
            INSERT INTO moves (cam_item_id, from_station, to_station, operator, notes)
            VALUES (?, 'new', ?, ?, 'Initial creation')
            """,
            (cam_item_id, item.status_station, config.DEFAULT_OPERATOR)
        )
        await db.commit()

        cursor = await db.execute("SELECT * FROM cam_items WHERE id = ?", (cam_item_id,))
        new_item = await cursor.fetchone()
        return dict(new_item)
    except aiosqlite.IntegrityError:
        raise HTTPException(
            status_code=400,
            detail=f"CAM item already exists: job_id={item.job_id}, set={item.set_no}, cam={item.cam_no}"
        )

@app.patch("/api/cam-items/{cam_item_id}")
async def update_cam_item(cam_item_id: int, item: CamItemUpdate, db: aiosqlite.Connection = Depends(get_db)):
    """Update a CAM item (die steel info, notes, etc.)"""
    updates = []
    values = []

    if item.die_position is not None:
        if item.die_position not in config.DIE_POSITIONS:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid die position. Must be one of: {', '.join(config.DIE_POSITIONS)}"
            )
        updates.append("die_position = ?")
        values.append(item.die_position)

    if item.enter_die_steel is not None:
        updates.append("enter_die_steel = ?")
        values.append(item.enter_die_steel)

    if item.exit_die_steel is not None:
        updates.append("exit_die_steel = ?")
        values.append(item.exit_die_steel)

    if item.notes is not None:
        updates.append("notes = ?")
        values.append(item.notes)

    if item.eol_cycles_expected is not None:
        updates.append("eol_cycles_expected = ?")
        values.append(item.eol_cycles_expected)

    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    values.append(cam_item_id)
    await db.execute(
        f"UPDATE cam_items SET {', '.join(updates)} WHERE id = ?",
        values
    )
    await db.commit()

    cursor = await db.execute("SELECT * FROM cam_items WHERE id = ?", (cam_item_id,))
    updated_item = await cursor.fetchone()

    if not updated_item:
        raise HTTPException(status_code=404, detail="CAM item not found")

    return dict(updated_item)

@app.post("/api/cam-items/bulk")
async def create_cam_items_bulk(bulk: CamItemBulkCreate, db: aiosqlite.Connection = Depends(get_db)):
    """Create multiple cam items for a job (sets x cams_per_set)"""
    if bulk.initial_station not in config.STATIONS:
        raise HTTPException(status_code=400, detail=f"Invalid station: {bulk.initial_station}")

    created_items = []

    for set_no in bulk.sets:
        for cam_no in range(1, bulk.cams_per_set + 1):
            try:
                cursor = await db.execute(
                    """
                    INSERT INTO cam_items
                    (job_id, set_no, cam_no, status_station)
                    VALUES (?, ?, ?, ?)
                    """,
                    (bulk.job_id, set_no, cam_no, bulk.initial_station)
                )
                cam_item_id = cursor.lastrowid

                # Record initial move
                await db.execute(
                    """
                    INSERT INTO moves (cam_item_id, from_station, to_station, operator, notes)
                    VALUES (?, 'new', ?, ?, 'Bulk creation')
                    """,
                    (cam_item_id, bulk.initial_station, config.DEFAULT_OPERATOR)
                )

                created_items.append({
                    "id": cam_item_id,
                    "set_no": set_no,
                    "cam_no": cam_no
                })
            except aiosqlite.IntegrityError:
                # Skip if already exists
                pass

    await db.commit()

    return {
        "success": True,
        "created_count": len(created_items),
        "items": created_items
    }

# ========== Entry Resolution Endpoint ==========

@app.post("/api/resolve-entry")
async def resolve_entry_endpoint(
    request: EntryResolveRequest,
    db: aiosqlite.Connection = Depends(get_db)
):
    """
    Resolve a manual entry string to CAM item(s).

    Returns:
    - status: exact | multiple | not_found | job_not_found
    - cam_item: if exact match
    - candidates: if multiple matches
    - job: job info
    """
    try:
        result = await resolve_entry(db, request.entry)
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

# ========== Move Operations Endpoints ==========

@app.post("/api/moves")
async def move_cam(move: MoveRequest, db: aiosqlite.Connection = Depends(get_db)):
    """Move a cam item to a new station"""
    try:
        result = await move_cam_to_station(
            db,
            cam_item_id=move.cam_item_id,
            to_station=move.to_station,
            operator=move.operator,
            notes=move.notes,
            auto_bump=move.auto_bump
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/api/moves/undo/{cam_item_id}")
async def undo_move(cam_item_id: int, db: aiosqlite.Connection = Depends(get_db)):
    """Undo the last move for a cam item"""
    try:
        result = await undo_last_move(db, cam_item_id)
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/api/moves")
async def list_moves(
    cam_item_id: Optional[int] = None,
    limit: int = 100,
    db: aiosqlite.Connection = Depends(get_db)
):
    """List recent moves"""
    if cam_item_id:
        cursor = await db.execute(
            """
            SELECT * FROM moves
            WHERE cam_item_id = ?
            ORDER BY moved_at DESC
            LIMIT ?
            """,
            (cam_item_id, limit)
        )
    else:
        cursor = await db.execute(
            """
            SELECT m.*, c.job_id, c.set_no, c.cam_no
            FROM moves m
            JOIN cam_items c ON m.cam_item_id = c.id
            ORDER BY m.moved_at DESC
            LIMIT ?
            """,
            (limit,)
        )

    moves = await cursor.fetchall()
    return [dict(m) for m in moves]

# ========== Hot List Endpoint ==========

@app.get("/api/hot-list")
async def get_hot_list(db: aiosqlite.Connection = Depends(get_db)):
    """Get the priority hot list"""
    hot_list = await generate_hot_list(db)
    return hot_list

# ========== Search Endpoint ==========

@app.get("/api/search")
async def search(
    q: str,
    db: aiosqlite.Connection = Depends(get_db)
):
    """Search for jobs and cam items by S-number, set, cam, or keywords"""
    search_term = f"%{q}%"

    # Search jobs
    cursor = await db.execute(
        """
        SELECT * FROM jobs
        WHERE s_number LIKE ? OR title LIKE ?
        ORDER BY s_number
        """,
        (search_term, search_term)
    )
    jobs = await cursor.fetchall()

    # Try to parse as entry and find cam items
    cam_items = []
    try:
        result = await resolve_entry(db, q)
        if result['status'] == 'exact':
            cam_items = [result['cam_item']]
        elif result['status'] == 'multiple':
            cam_items = result['candidates']
    except:
        pass

    return {
        "query": q,
        "jobs": [dict(j) for j in jobs],
        "cam_items": cam_items
    }

# ========== Analytics Endpoints ==========

@app.get("/api/analytics/station-counts")
async def analytics_station_counts(db: aiosqlite.Connection = Depends(get_db)):
    """Get counts of tools by station"""
    cursor = await db.execute(
        """
        SELECT
            status_station as station,
            COUNT(*) as count
        FROM cam_items
        GROUP BY status_station
        ORDER BY
            CASE status_station
                WHEN 'active' THEN 1
                WHEN 'sharpen' THEN 2
                WHEN 'cabinet' THEN 3
                WHEN 'refill' THEN 4
            END
        """
    )
    counts = await cursor.fetchall()
    return [dict(c) for c in counts]

@app.get("/api/analytics/moves-recent")
async def analytics_moves_recent(days: int = 7, db: aiosqlite.Connection = Depends(get_db)):
    """Get move counts for recent days"""
    cursor = await db.execute(
        """
        SELECT
            DATE(moved_at) as date,
            COUNT(*) as count
        FROM moves
        WHERE moved_at >= datetime('now', '-' || ? || ' days')
        AND undone = 0
        GROUP BY DATE(moved_at)
        ORDER BY date DESC
        """,
        (days,)
    )
    moves = await cursor.fetchall()
    return [dict(m) for m in moves]

@app.get("/api/analytics/dwell-times")
async def analytics_dwell_times(db: aiosqlite.Connection = Depends(get_db)):
    """Get average dwell time per station (in hours)"""
    cursor = await db.execute(
        """
        SELECT
            c.status_station as station,
            AVG(
                (julianday('now') - julianday(c.status_updated_at)) * 24
            ) as avg_hours,
            COUNT(*) as count
        FROM cam_items c
        GROUP BY c.status_station
        """
    )
    times = await cursor.fetchall()
    return [dict(t) for t in times]

@app.get("/api/analytics/cycle-counts")
async def analytics_cycle_counts(limit: int = 20, db: aiosqlite.Connection = Depends(get_db)):
    """Get cycle counts (moves into 'active') per cam item"""
    cursor = await db.execute(
        """
        SELECT
            c.id as cam_item_id,
            j.s_number,
            c.set_no,
            c.cam_no,
            COUNT(m.id) as cycle_count
        FROM cam_items c
        JOIN jobs j ON c.job_id = j.id
        LEFT JOIN moves m ON c.id = m.cam_item_id AND m.to_station = 'active' AND m.undone = 0
        GROUP BY c.id
        ORDER BY cycle_count DESC
        LIMIT ?
        """,
        (limit,)
    )
    cycles = await cursor.fetchall()
    return [dict(c) for c in cycles]

@app.get("/api/analytics/sharpen-backlog")
async def analytics_sharpen_backlog(db: aiosqlite.Connection = Depends(get_db)):
    """Get sharpen backlog stats"""
    cursor = await db.execute(
        """
        SELECT
            COUNT(*) as backlog_size,
            AVG(
                (julianday('now') - julianday(status_updated_at)) * 24
            ) as avg_hours_in_sharpen
        FROM cam_items
        WHERE status_station = 'sharpen'
        """
    )
    stats = await cursor.fetchone()
    return dict(stats)

# ========== Configuration Endpoints ==========

@app.get("/api/config")
async def get_config():
    """Get current configuration"""
    auto_bump = await get_config_value('auto_bump_enabled', 'false')
    return {
        "auto_bump_enabled": auto_bump.lower() == 'true'
    }

@app.patch("/api/config")
async def update_config(config_update: ConfigUpdate):
    """Update configuration"""
    if config_update.auto_bump_enabled is not None:
        await set_config_value(
            'auto_bump_enabled',
            'true' if config_update.auto_bump_enabled else 'false'
        )

    return await get_config()

# ========== Export Endpoints ==========

@app.get("/api/export/jobs/csv")
async def export_jobs_csv(db: aiosqlite.Connection = Depends(get_db)):
    """Export jobs to CSV"""
    cursor = await db.execute("SELECT * FROM jobs ORDER BY s_number")
    jobs = await cursor.fetchall()

    output = io.StringIO()
    writer = csv.writer(output)

    # Write header
    writer.writerow(['id', 's_number', 'title', 'priority_base', 'created_at', 'notes'])

    # Write data
    for job in jobs:
        writer.writerow([
            job['id'], job['s_number'], job['title'],
            job['priority_base'], job['created_at'], job['notes']
        ])

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=jobs.csv"}
    )

@app.get("/api/export/cam-items/csv")
async def export_cam_items_csv(db: aiosqlite.Connection = Depends(get_db)):
    """Export cam items to CSV"""
    cursor = await db.execute(
        """
        SELECT
            c.*,
            j.s_number
        FROM cam_items c
        JOIN jobs j ON c.job_id = j.id
        ORDER BY j.s_number, c.set_no, c.cam_no
        """
    )
    items = await cursor.fetchall()

    output = io.StringIO()
    writer = csv.writer(output)

    # Write header
    writer.writerow([
        's_number', 'set_no', 'cam_no', 'status_station', 'status_updated_at',
        'enter_die_steel', 'exit_die_steel', 'notes', 'eol_cycles_expected'
    ])

    # Write data
    for item in items:
        writer.writerow([
            item['s_number'], item['set_no'], item['cam_no'],
            item['status_station'], item['status_updated_at'],
            item['enter_die_steel'], item['exit_die_steel'],
            item['notes'], item['eol_cycles_expected']
        ])

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=cam_items.csv"}
    )

@app.get("/api/export/moves/csv")
async def export_moves_csv(db: aiosqlite.Connection = Depends(get_db)):
    """Export moves to CSV"""
    cursor = await db.execute(
        """
        SELECT
            m.*,
            j.s_number,
            c.set_no,
            c.cam_no
        FROM moves m
        JOIN cam_items c ON m.cam_item_id = c.id
        JOIN jobs j ON c.job_id = j.id
        ORDER BY m.moved_at DESC
        """
    )
    moves = await cursor.fetchall()

    output = io.StringIO()
    writer = csv.writer(output)

    # Write header
    writer.writerow([
        's_number', 'set_no', 'cam_no', 'from_station', 'to_station',
        'moved_at', 'operator', 'notes', 'undone'
    ])

    # Write data
    for move in moves:
        writer.writerow([
            move['s_number'], move['set_no'], move['cam_no'],
            move['from_station'], move['to_station'],
            move['moved_at'], move['operator'], move['notes'], move['undone']
        ])

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=moves.csv"}
    )

@app.get("/api/export/database")
async def export_database():
    """Download the entire SQLite database file"""
    return FileResponse(
        config.DATABASE_PATH,
        media_type="application/octet-stream",
        filename="cam_tracking_backup.db"
    )

if __name__ == "__main__":
    import uvicorn

    # Initialize database if it doesn't exist
    init_database()

    # Run server
    uvicorn.run(
        "main:app",
        host=config.HOST,
        port=config.PORT,
        reload=False
    )
