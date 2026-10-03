"""Jobs CRUD."""
import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Request

from app.cache import cache
from app.deps import get_admin_user
from app.limiter import limiter
from app.schemas import JobCreate, JobUpdate
from app.utils import (CAM_ITEM_COLUMNS, JOB_COLUMNS, fetch_all, fetch_one, record_priority_change,
                       require_one, update_row)
from business_logic import get_tool_stats
from database import get_db

router = APIRouter(prefix="/api/jobs")


async def _get_job_or_404(db, job_id: int) -> dict:
    return await require_one(db, f"SELECT {JOB_COLUMNS} FROM jobs WHERE id = ?", (job_id,), "Job not found")


@router.get("")
@limiter.limit("100/minute")
async def list_jobs(request: Request, skip: int = 0, limit: int = 100,
                    db: aiosqlite.Connection = Depends(get_db)):
    """List jobs, newest first (limit is capped at 500)."""
    limit = min(limit, 500)
    total = (await fetch_one(db, "SELECT COUNT(*) as count FROM jobs"))['count']
    items = await fetch_all(
        db, f"SELECT {JOB_COLUMNS} FROM jobs ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
        (limit, skip)
    )
    return {"items": items, "total": total, "skip": skip, "limit": limit, "has_more": (skip + limit) < total}


@router.get("/{job_id}")
async def get_job(job_id: int, db: aiosqlite.Connection = Depends(get_db)):
    """A job with its cam items."""
    job = await _get_job_or_404(db, job_id)
    cam_items = await fetch_all(
        db, f"SELECT {CAM_ITEM_COLUMNS} FROM cam_items WHERE job_id = ? ORDER BY set_no, cam_no", (job_id,)
    )
    return {"job": job, "cam_items": cam_items}


@router.get("/{job_id}/tool-stats")
async def get_job_tool_stats(job_id: int, db: aiosqlite.Connection = Depends(get_db)):
    """Sharpening and lifespan stats for every tool in a job (one round trip)."""
    await _get_job_or_404(db, job_id)
    return await get_tool_stats(db, job_id=job_id)


@router.post("")
@limiter.limit("50/minute")
async def create_job(request: Request, job: JobCreate, db: aiosqlite.Connection = Depends(get_db),
                     admin_user: dict = Depends(get_admin_user)):
    try:
        cursor = await db.execute(
            "INSERT INTO jobs (s_number, title, priority_level, notes) VALUES (?, ?, ?, ?)",
            (job.s_number, job.title, job.priority_level, job.notes)
        )
        await db.commit()
    except aiosqlite.IntegrityError:
        raise HTTPException(status_code=400, detail=f"Job {job.s_number} already exists")
    cache.invalidate()
    return await _get_job_or_404(db, cursor.lastrowid)


@router.patch("/{job_id}")
async def update_job(job_id: int, job: JobUpdate, db: aiosqlite.Connection = Depends(get_db),
                     admin_user: dict = Depends(get_admin_user)):
    """Update a job. Changing priority requires a reason and is audit-logged."""
    existing = await _get_job_or_404(db, job_id)
    old_priority = existing['priority_level'] or 'low'

    fields = job.model_dump(exclude_none=True, include={"title", "notes"})
    priority_changed = job.priority_level is not None and job.priority_level != old_priority
    if not fields and job.priority_level is None:
        raise HTTPException(status_code=400, detail="No fields to update")
    if priority_changed and not (job.reason and job.reason.strip()):
        raise HTTPException(status_code=400, detail="Reason is required when changing priority")

    if fields:
        await update_row(db, "jobs", job_id, fields)
    if priority_changed:
        await record_priority_change(db, job_id, admin_user, old_priority, job.priority_level,
                                     job.reason.strip(), job.source or 'all_jobs')
    await db.commit()
    cache.invalidate()
    return await _get_job_or_404(db, job_id)


@router.delete("/{job_id}")
async def delete_job(job_id: int, db: aiosqlite.Connection = Depends(get_db),
                     admin_user: dict = Depends(get_admin_user)):
    """Delete a job (cascades to cam items, moves and lifespans)."""
    await _get_job_or_404(db, job_id)
    await db.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
    await db.commit()
    cache.invalidate()
    return {"success": True, "message": "Job deleted"}
