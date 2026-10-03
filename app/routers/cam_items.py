"""CAM item (tool) CRUD and per-tool stats."""
from typing import Optional

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Request

import config
from app.cache import cache
from app.deps import get_admin_user
from app.limiter import limiter
from app.schemas import CamItemBulkCreate, CamItemCreate, CamItemUpdate
from app.utils import (CAM_ITEM_COLUMNS, JOB_COLUMNS, MOVE_COLUMNS, fetch_all, fetch_one,
                       require_one, update_row)
from business_logic import get_lifespan_forecast, get_tool_stats
from database import get_config_value, get_db

router = APIRouter(prefix="/api/cam-items")


async def _default_material_life(db) -> float:
    return float(await get_config_value('default_material_life', str(config.DEFAULT_MATERIAL_LIFE), db=db))


async def _get_cam_or_404(db, cam_item_id: int) -> dict:
    return await require_one(db, f"SELECT {CAM_ITEM_COLUMNS} FROM cam_items WHERE id = ?",
                             (cam_item_id,), "CAM item not found")


@router.get("")
@limiter.limit("150/minute")
async def list_cam_items(request: Request, job_id: Optional[int] = None, station: Optional[str] = None,
                         skip: int = 0, limit: int = 100, db: aiosqlite.Connection = Depends(get_db)):
    """List cam items with optional job/station filters (limit is capped at 500)."""
    limit = min(limit, 500)
    conditions, params = [], []
    if job_id:
        conditions.append("job_id = ?")
        params.append(job_id)
    if station:
        if station not in config.STATIONS:
            raise HTTPException(status_code=400,
                                detail=f"Invalid station. Must be one of: {', '.join(config.STATIONS)}")
        conditions.append("status_station = ?")
        params.append(station)
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    total = (await fetch_one(db, f"SELECT COUNT(*) as count FROM cam_items {where}", params))['count']
    items = await fetch_all(
        db,
        f"SELECT {CAM_ITEM_COLUMNS} FROM cam_items {where} ORDER BY status_updated_at DESC LIMIT ? OFFSET ?",
        params + [limit, skip]
    )
    return {
        "items": items,
        "total": total,
        "skip": skip,
        "limit": limit,
        "has_more": (skip + limit) < total,
        "filters": {"job_id": job_id, "station": station}
    }


@router.get("/{cam_item_id}")
async def get_cam_item(cam_item_id: int, db: aiosqlite.Connection = Depends(get_db)):
    """A cam item with its job and full move history."""
    cam_item = await _get_cam_or_404(db, cam_item_id)
    moves = await fetch_all(
        db, f"SELECT {MOVE_COLUMNS} FROM moves WHERE cam_item_id = ? ORDER BY moved_at DESC, id DESC",
        (cam_item_id,)
    )
    job = await fetch_one(db, f"SELECT {JOB_COLUMNS} FROM jobs WHERE id = ?", (cam_item['job_id'],))
    return {"cam_item": cam_item, "job": job, "moves": moves}


@router.post("")
async def create_cam_item(item: CamItemCreate, db: aiosqlite.Connection = Depends(get_db),
                          admin_user: dict = Depends(get_admin_user)):
    material_life = item.max_material_life or await _default_material_life(db)
    try:
        cursor = await db.execute(
            """INSERT INTO cam_items
               (job_id, set_no, cam_no, die_position, enter_die_steel, exit_die_steel,
                status_station, notes, eol_cycles_expected, max_material_life)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (item.job_id, item.set_no, item.cam_no, item.die_position, item.enter_die_steel,
             item.exit_die_steel, item.status_station, item.notes, item.eol_cycles_expected, material_life)
        )
    except aiosqlite.IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=400,
            detail=f"CAM item already exists: job_id={item.job_id}, set={item.set_no}, cam={item.cam_no}"
        )
    cam_item_id = cursor.lastrowid
    await db.execute(
        "INSERT INTO moves (cam_item_id, from_station, to_station, operator, notes) VALUES (?, 'new', ?, ?, 'Initial creation')",
        (cam_item_id, item.status_station, config.DEFAULT_OPERATOR)
    )
    await db.execute(
        "INSERT INTO tool_lifespans (cam_item_id, lifespan_number, max_material_life) VALUES (?, 1, ?)",
        (cam_item_id, material_life)
    )
    await db.commit()
    cache.invalidate()
    return await _get_cam_or_404(db, cam_item_id)


@router.patch("/{cam_item_id}")
async def update_cam_item(cam_item_id: int, item: CamItemUpdate, db: aiosqlite.Connection = Depends(get_db),
                          admin_user: dict = Depends(get_admin_user)):
    """Update tool metadata (die steel, notes, material life...)."""
    await _get_cam_or_404(db, cam_item_id)
    await update_row(db, "cam_items", cam_item_id, item.model_dump(exclude_none=True))
    await db.commit()
    return await _get_cam_or_404(db, cam_item_id)


@router.post("/bulk")
async def create_cam_items_bulk(bulk: CamItemBulkCreate, db: aiosqlite.Connection = Depends(get_db),
                                admin_user: dict = Depends(get_admin_user)):
    """Create num_sets x configured cams for a job. Existing tools are left untouched."""
    initial_station = "cabinet"
    default_life = await _default_material_life(db)

    await db.executemany(
        """INSERT OR IGNORE INTO cam_items
           (job_id, set_no, cam_no, die_position, enter_die_steel, exit_die_steel, status_station, max_material_life)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        [(bulk.job_id, set_no, cam.cam_no, cam.die_position, cam.enter_die_steel, cam.exit_die_steel,
          initial_station, default_life)
         for set_no in range(1, bulk.num_sets + 1)
         for cam in bulk.cams]
    )

    sets_list = list(range(1, bulk.num_sets + 1))
    cam_nos = [cam.cam_no for cam in bulk.cams]
    rows = await fetch_all(
        db,
        f"""SELECT id, set_no, cam_no FROM cam_items
            WHERE job_id = ? AND set_no IN ({",".join("?" * len(sets_list))})
              AND cam_no IN ({",".join("?" * len(cam_nos))})
            ORDER BY set_no, cam_no""",
        [bulk.job_id] + sets_list + cam_nos
    )

    # Tools without any move history were just created: give them a creation record and lifespan
    ids = [r['id'] for r in rows]
    if ids:
        existing = await fetch_all(
            db, f"SELECT DISTINCT cam_item_id FROM moves WHERE cam_item_id IN ({','.join('?' * len(ids))})", ids
        )
        has_history = {r['cam_item_id'] for r in existing}
        new_ids = [i for i in ids if i not in has_history]
        if new_ids:
            await db.executemany(
                "INSERT INTO moves (cam_item_id, from_station, to_station, operator, notes) VALUES (?, 'new', ?, ?, 'Bulk creation')",
                [(i, initial_station, config.DEFAULT_OPERATOR) for i in new_ids]
            )
            await db.executemany(
                "INSERT OR IGNORE INTO tool_lifespans (cam_item_id, lifespan_number, max_material_life) VALUES (?, 1, ?)",
                [(i, default_life) for i in new_ids]
            )

    await db.commit()
    cache.invalidate()
    return {"success": True, "created_count": len(rows), "items": rows}


@router.get("/{cam_item_id}/sharpen-stats")
async def get_sharpen_stats(cam_item_id: int, db: aiosqlite.Connection = Depends(get_db)):
    """Sharpening statistics for a CAM item."""
    stats = await get_tool_stats(db, cam_item_id=cam_item_id)
    if not stats:
        return {"sharpen_count": 0, "avg_material_removed": None,
                "last_material_removed": None, "last_sharpen_date": None}
    s = stats[0]
    return {k: s[k] for k in ("sharpen_count", "avg_material_removed", "last_material_removed", "last_sharpen_date")}


@router.get("/{cam_item_id}/lifespan")
async def get_cam_lifespan(cam_item_id: int, db: aiosqlite.Connection = Depends(get_db)):
    """Lifespan tracking and forecast for a CAM item."""
    await require_one(db, "SELECT id FROM cam_items WHERE id = ?", (cam_item_id,), "CAM item not found")
    return await get_lifespan_forecast(db, cam_item_id)
