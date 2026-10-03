"""Hot list, Top 5 management and the priority-change audit log."""
import json
import logging
from datetime import datetime, timedelta
from typing import Optional

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException

import config
from app.cache import cache
from app.deps import get_admin_user
from app.schemas import Top5ReorderRequest, Top5UpdateRequest
from app.utils import fetch_all, fetch_one, record_priority_change
from business_logic import generate_all_jobs_ranked, generate_hot_list
from database import get_config_value, get_db, set_config_value

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api")

TOP5_SIZE = 5
# Top 5 priority is tied to position: #1=top ... #5=low
POSITION_PRIORITIES = ['top', 'urgent', 'high', 'medium', 'low']


@router.get("/hot-list")
async def get_hot_list(db: aiosqlite.Connection = Depends(get_db)):
    """The priority hot list (cached for 30s)."""
    hot_list = cache.get("hot_list")
    if hot_list is None:
        hot_list = await generate_hot_list(db)
        cache.set("hot_list", hot_list)
    return hot_list


@router.get("/top5")
async def get_top5(db: aiosqlite.Connection = Depends(get_db), admin_user: dict = Depends(get_admin_user)):
    """Top 5 jobs (saved manual order, topped up from the ranking) plus swap candidates."""
    all_ranked = await generate_all_jobs_ranked(db)
    top5 = all_ranked[:TOP5_SIZE]

    saved_order = await get_config_value("top5_order", db=db)
    if saved_order:
        try:
            ranked_map = {item['job_id']: item for item in all_ranked}
            ordered = [ranked_map[jid] for jid in json.loads(saved_order) if jid in ranked_map]
            seen = {item['job_id'] for item in ordered}
            ordered += [item for item in all_ranked if item['job_id'] not in seen]
            top5 = ordered[:TOP5_SIZE]
        except (json.JSONDecodeError, TypeError):
            logger.warning("Ignoring malformed top5_order config value")

    top5_ids = {item['job_id'] for item in top5}
    return {
        "top5": top5,
        "remaining_hot_list": [item for item in all_ranked if item['job_id'] not in top5_ids],
        "all_jobs": await fetch_all(db, "SELECT id, s_number, title, priority_level FROM jobs ORDER BY s_number")
    }


@router.post("/top5/set-priority")
async def set_top5_priorities(request_body: Top5UpdateRequest, db: aiosqlite.Connection = Depends(get_db),
                              admin_user: dict = Depends(get_admin_user)):
    """Batch update priorities for Top 5 jobs with audit logging."""
    if len(request_body.jobs) > TOP5_SIZE:
        raise HTTPException(status_code=400, detail=f"Maximum {TOP5_SIZE} jobs allowed")

    results = []
    for entry in request_body.jobs:
        job_id = entry.get("job_id")
        new_priority = entry.get("priority_level")
        reason = (entry.get("reason") or "").strip()
        if not job_id or not new_priority:
            continue
        if new_priority not in config.PRIORITY_LEVELS:
            results.append({"job_id": job_id, "error": f"Invalid priority: {new_priority}"})
            continue

        row = await fetch_one(db, "SELECT priority_level FROM jobs WHERE id = ?", (job_id,))
        if not row:
            results.append({"job_id": job_id, "error": "Job not found"})
            continue

        old_priority = row['priority_level'] or 'low'
        if old_priority == new_priority:
            continue
        if not reason:
            results.append({"job_id": job_id, "error": "Reason required for priority change"})
            continue
        await record_priority_change(db, job_id, admin_user, old_priority, new_priority, reason, 'top5')
        results.append({"job_id": job_id, "old": old_priority, "new": new_priority})

    await db.commit()
    cache.invalidate()
    return {"success": True, "changes": results}


@router.post("/top5/reorder")
async def reorder_top5(request_body: Top5ReorderRequest, db: aiosqlite.Connection = Depends(get_db),
                       admin_user: dict = Depends(get_admin_user)):
    """Save a manual Top 5 order; each job's priority follows its position."""
    if len(request_body.job_ids) > TOP5_SIZE:
        raise HTTPException(status_code=400, detail=f"Maximum {TOP5_SIZE} jobs allowed")

    await set_config_value("top5_order", json.dumps(request_body.job_ids), db=db)

    changes = []
    for job_id, new_priority in zip(request_body.job_ids, POSITION_PRIORITIES):
        row = await fetch_one(db, "SELECT priority_level FROM jobs WHERE id = ?", (job_id,))
        if not row:
            continue
        old_priority = row['priority_level'] or 'low'
        if old_priority != new_priority:
            await record_priority_change(db, job_id, admin_user, old_priority, new_priority,
                                         'Reordered in Top 5', 'top5')
            changes.append({"job_id": job_id, "old": old_priority, "new": new_priority})

    await db.commit()
    cache.invalidate()
    logger.info(f"Admin '{admin_user['username']}' reordered Top 5: {request_body.job_ids} "
                f"({len(changes)} priority changes)")
    return {"success": True, "order": request_body.job_ids, "priority_changes": changes}


@router.get("/priority-changes")
async def get_priority_changes(job_id: Optional[int] = None, limit: int = 50, skip: int = 0,
                               db: aiosqlite.Connection = Depends(get_db),
                               admin_user: dict = Depends(get_admin_user)):
    """Priority change audit log, optionally filtered by job."""
    where, params = ("WHERE pc.job_id = ?", [job_id]) if job_id is not None else ("", [])
    items = await fetch_all(
        db,
        f"""SELECT pc.id, pc.job_id, pc.changed_by_user_id, pc.changed_by_username,
                   pc.old_priority, pc.new_priority, pc.reason, pc.source, pc.changed_at,
                   j.s_number, j.title
            FROM priority_changes pc
            JOIN jobs j ON pc.job_id = j.id
            {where}
            ORDER BY pc.changed_at DESC, pc.id DESC
            LIMIT ? OFFSET ?""",
        params + [limit, skip]
    )
    total = (await fetch_one(db, f"SELECT COUNT(*) as total FROM priority_changes pc {where}", params))['total']
    return {"items": items, "total": total}


@router.get("/priority-changes/analytics")
async def get_priority_analytics(days: int = 30, db: aiosqlite.Connection = Depends(get_db),
                                 admin_user: dict = Depends(get_admin_user)):
    """Priority change trends, most-escalated jobs and transition counts."""
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()
    params = (cutoff,)

    return {
        "period_days": days,
        "total_changes": (await fetch_one(
            db, "SELECT COUNT(*) as total FROM priority_changes WHERE changed_at >= ?", params))['total'],
        "daily_counts": await fetch_all(db, """
            SELECT DATE(changed_at) as day, COUNT(*) as count
            FROM priority_changes WHERE changed_at >= ?
            GROUP BY DATE(changed_at) ORDER BY day""", params),
        "most_changed_jobs": await fetch_all(db, """
            SELECT j.id, j.s_number, j.title, j.priority_level,
                   COUNT(*) as change_count,
                   COUNT(CASE WHEN pc.new_priority IN ('urgent', 'top') THEN 1 END) as escalation_count
            FROM priority_changes pc
            JOIN jobs j ON pc.job_id = j.id
            WHERE pc.changed_at >= ?
            GROUP BY pc.job_id
            ORDER BY escalation_count DESC, change_count DESC
            LIMIT 10""", params),
        "transitions": await fetch_all(db, """
            SELECT old_priority, new_priority, COUNT(*) as count
            FROM priority_changes WHERE changed_at >= ?
            GROUP BY old_priority, new_priority ORDER BY count DESC""", params),
        "by_source": await fetch_all(db, """
            SELECT source, COUNT(*) as count
            FROM priority_changes WHERE changed_at >= ?
            GROUP BY source""", params),
    }
