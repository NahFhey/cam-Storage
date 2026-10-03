"""Entry resolution and station moves."""
import logging
from typing import Optional

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Request

from app.cache import cache
from app.deps import get_current_user
from app.limiter import limiter
from app.schemas import BatchMoveRequest, EntryResolveRequest, MoveRequest, SetMoveRequest
from app.utils import MOVE_COLUMNS, fetch_all
from business_logic import CamNotFoundError, move_cam_to_station, move_set_to_station, undo_last_move
from database import get_db
from entry_parser import resolve_entry

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api")


def _http_error(e: Exception) -> HTTPException:
    if isinstance(e, CamNotFoundError):
        return HTTPException(status_code=404, detail="CAM item not found")
    return HTTPException(status_code=400, detail=str(e))


@router.post("/resolve-entry")
@limiter.limit("200/minute")
async def resolve_entry_endpoint(request: Request, body: EntryResolveRequest,
                                 db: aiosqlite.Connection = Depends(get_db)):
    """
    Resolve a manual entry string to CAM item(s).

    status: exact | multiple | not_found | job_not_found
    """
    try:
        return await resolve_entry(db, body.entry)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/moves")
@limiter.limit("200/minute")
async def move_cam(request: Request, move: MoveRequest, db: aiosqlite.Connection = Depends(get_db),
                   user: dict = Depends(get_current_user)):
    """Move a cam item to a new station. The logged-in user is recorded as operator."""
    try:
        result = await move_cam_to_station(
            db,
            cam_item_id=move.cam_item_id,
            to_station=move.to_station,
            operator=user["display_name"],
            notes=move.notes,
            auto_bump=move.auto_bump,
            material_removed=move.material_removed
        )
    except ValueError as e:
        raise _http_error(e)
    except Exception as e:
        logger.error(f"Move failed for cam {move.cam_item_id}: {type(e).__name__}: {e}")
        raise HTTPException(status_code=500, detail=f"Internal error: {type(e).__name__}: {e}")
    cache.invalidate()
    return result


@router.post("/moves/set")
@limiter.limit("100/minute")
async def move_set(request: Request, body: SetMoveRequest, db: aiosqlite.Connection = Depends(get_db),
                   user: dict = Depends(get_current_user)):
    """Move a whole set atomically. Moving to Active bumps conflicting active sets to Sharpen."""
    try:
        result = await move_set_to_station(
            db, body.job_id, body.set_no, body.to_station,
            operator=user["display_name"],
            material_removed=body.material_removed
        )
    except ValueError as e:
        raise _http_error(e)
    cache.invalidate()
    return result


@router.post("/moves/undo/{cam_item_id}")
async def undo_move(cam_item_id: int, db: aiosqlite.Connection = Depends(get_db),
                    user: dict = Depends(get_current_user)):
    """Undo the last move for a cam item."""
    try:
        result = await undo_last_move(db, cam_item_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    cache.invalidate()
    return result


@router.post("/moves/batch")
@limiter.limit("50/minute")
async def batch_move(request: Request, batch: BatchMoveRequest, db: aiosqlite.Connection = Depends(get_db),
                     user: dict = Depends(get_current_user)):
    """Move multiple cam items. Each move is independent: failures are reported, successes kept."""
    results, errors = [], []
    for item in batch.moves:
        try:
            results.append(await move_cam_to_station(
                db,
                cam_item_id=item.cam_item_id,
                to_station=item.to_station,
                operator=user["display_name"],
                notes=item.notes,
                auto_bump=batch.auto_bump,
                material_removed=item.material_removed
            ))
        except Exception as e:
            message = "CAM item not found" if isinstance(e, CamNotFoundError) else str(e)
            errors.append({"cam_item_id": item.cam_item_id, "error": message})

    if results:
        cache.invalidate()
    return {
        "success": not errors,
        "moved": len(results),
        "failed": len(errors),
        "results": results,
        "errors": errors
    }


@router.get("/moves")
async def list_moves(cam_item_id: Optional[int] = None, limit: int = 100,
                     db: aiosqlite.Connection = Depends(get_db)):
    """Recent moves, newest first, with the tool's identity."""
    limit = min(limit, 500)
    where, params = ("WHERE m.cam_item_id = ?", [cam_item_id]) if cam_item_id else ("", [])
    columns = ", ".join(f"m.{c.strip()}" for c in MOVE_COLUMNS.split(","))
    return await fetch_all(
        db,
        f"""SELECT {columns}, c.job_id, c.set_no, c.cam_no, j.s_number
            FROM moves m
            JOIN cam_items c ON m.cam_item_id = c.id
            JOIN jobs j ON c.job_id = j.id
            {where}
            ORDER BY m.moved_at DESC, m.id DESC
            LIMIT ?""",
        params + [limit]
    )
