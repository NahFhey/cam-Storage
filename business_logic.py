"""
Business logic for CAM tracking operations:
- Station moves (single tool and whole set) with auto-bump
- Undo functionality
- Priority calculation and hot list generation
- Tool lifespan tracking and forecasting
"""
import logging
from math import floor
from typing import Dict, List, Optional, Tuple

import config
from database import get_config_value

logger = logging.getLogger(__name__)


class CamNotFoundError(ValueError):
    """Raised when a CAM item (or set) does not exist."""


# Columns returned whenever a cam item is serialized
CAM_ITEM_COLUMNS = """id, job_id, set_no, cam_no, die_position, enter_die_steel, exit_die_steel,
       status_station, status_updated_at, notes, eol_cycles_expected, max_material_life, created_at"""


def requires_material_removed(from_station: str, to_station: str) -> bool:
    """A sharpen -> cabinet move records how much material was removed."""
    return from_station == 'sharpen' and to_station == 'cabinet'


# ========== Station Moves ==========

async def _resolve_auto_bump(db, auto_bump: Optional[bool]) -> bool:
    if auto_bump is not None:
        return auto_bump
    value = await get_config_value('auto_bump_enabled', 'false', db=db)
    return value.lower() == 'true'


async def _set_station(db, cam_item_id: int, station: str):
    await db.execute(
        "UPDATE cam_items SET status_station = ?, status_updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (station, cam_item_id)
    )


async def _insert_move(db, cam_item_id: int, from_station: str, to_station: str,
                       operator: str, notes: Optional[str] = None,
                       material_removed: Optional[float] = None) -> int:
    cursor = await db.execute(
        """INSERT INTO moves (cam_item_id, from_station, to_station, operator, notes, material_removed)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (cam_item_id, from_station, to_station, operator, notes, material_removed)
    )
    return cursor.lastrowid


async def _apply_lifespan_tracking(db, cam_item_id: int, from_station: str, to_station: str,
                                   material_removed: Optional[float]):
    if requires_material_removed(from_station, to_station) and material_removed is not None:
        await update_lifespan_on_sharpen(db, cam_item_id, material_removed)
    if to_station == 'refill':
        await close_lifespan_on_refill(db, cam_item_id)
    if from_station == 'refill':
        await open_new_lifespan(db, cam_item_id)


async def _apply_move(db, cam_item_id: int, to_station: str, operator: str,
                      notes: Optional[str] = None, auto_bump: bool = False,
                      material_removed: Optional[float] = None) -> Dict:
    """Perform a single move inside the caller's transaction (no commit)."""
    if to_station not in config.STATIONS:
        raise ValueError(f"Invalid station: {to_station}")

    cursor = await db.execute(
        "SELECT id, job_id, set_no, cam_no, die_position, status_station FROM cam_items WHERE id = ?",
        (cam_item_id,)
    )
    cam_item = await cursor.fetchone()
    if not cam_item:
        raise CamNotFoundError(f"CAM item not found: {cam_item_id}")

    from_station = cam_item['status_station']
    if from_station == to_station:
        raise ValueError(f"CAM is already in {to_station}")
    if requires_material_removed(from_station, to_station) and material_removed is None:
        raise ValueError("Material removed must be specified when moving from Sharpen to Cabinet")

    auto_bumped = None
    # Only one cam per die position may be active across *different* sets of a job.
    # Multiple cams from the same set may be active simultaneously.
    if auto_bump and to_station == 'active' and cam_item['die_position']:
        cursor = await db.execute(
            """SELECT id, set_no, cam_no FROM cam_items
               WHERE job_id = ? AND set_no != ? AND die_position = ?
                 AND status_station = 'active' AND id != ?""",
            (cam_item['job_id'], cam_item['set_no'], cam_item['die_position'], cam_item_id)
        )
        existing_active = await cursor.fetchone()
        if existing_active:
            await _set_station(db, existing_active['id'], 'sharpen')
            bump_move_id = await _insert_move(
                db, existing_active['id'], 'active', 'sharpen', operator, 'Auto-bumped by system'
            )
            auto_bumped = {
                "cam_item_id": existing_active['id'],
                "move_id": bump_move_id,
                "from_station": "active",
                "to_station": "sharpen",
                "set_no": existing_active['set_no'],
                "cam_no": existing_active['cam_no']
            }

    await _set_station(db, cam_item_id, to_station)
    move_id = await _insert_move(db, cam_item_id, from_station, to_station, operator, notes, material_removed)
    await _apply_lifespan_tracking(db, cam_item_id, from_station, to_station, material_removed)

    return {
        "success": True,
        "cam_item_id": cam_item_id,
        "set_no": cam_item['set_no'],
        "cam_no": cam_item['cam_no'],
        "move_id": move_id,
        "from_station": from_station,
        "to_station": to_station,
        "auto_bumped": auto_bumped
    }


async def move_cam_to_station(
    db,
    cam_item_id: int,
    to_station: str,
    operator: str = None,
    notes: str = None,
    auto_bump: bool = None,
    material_removed: float = None
) -> Dict:
    """
    Move a CAM item to a new station (atomic: move, auto-bump and lifespan
    bookkeeping commit together or not at all).

    If auto_bump is enabled (explicitly or via config) and moving to 'active',
    an active cam from a different set with the same die position is bumped
    to 'sharpen'.
    """
    try:
        result = await _apply_move(
            db, cam_item_id, to_station,
            operator=operator or config.DEFAULT_OPERATOR,
            notes=notes,
            auto_bump=await _resolve_auto_bump(db, auto_bump),
            material_removed=material_removed
        )
        await db.commit()
        return result
    except Exception:
        await db.rollback()
        raise


async def move_set_to_station(
    db,
    job_id: int,
    set_no: int,
    to_station: str,
    operator: str = None,
    material_removed: Optional[Dict[int, float]] = None
) -> Dict:
    """
    Move every cam in a set to a station in a single transaction.

    When moving to 'active', any *other* set that has an active cam sharing a
    die position with this set is bumped to 'sharpen' in its entirety.
    Cams already at the destination are skipped. Sharpen -> cabinet moves need
    an entry in ``material_removed`` (keyed by cam_item_id).
    """
    operator = operator or config.DEFAULT_OPERATOR
    material_removed = material_removed or {}

    if to_station not in config.STATIONS:
        raise ValueError(f"Invalid station: {to_station}")

    cursor = await db.execute(
        """SELECT id, cam_no, die_position, status_station FROM cam_items
           WHERE job_id = ? AND set_no = ? ORDER BY cam_no""",
        (job_id, set_no)
    )
    cams = [dict(r) for r in await cursor.fetchall()]
    if not cams:
        raise CamNotFoundError(f"No CAMs found for set {set_no}")

    to_move = [c for c in cams if c['status_station'] != to_station]
    missing = [c['cam_no'] for c in to_move
               if requires_material_removed(c['status_station'], to_station)
               and material_removed.get(c['id']) is None]
    if missing:
        raise ValueError(
            "Material removed must be specified when moving from Sharpen to Cabinet "
            f"(Cam {', '.join(str(n) for n in missing)})"
        )

    try:
        bumped = []
        if to_station == 'active':
            die_positions = sorted({c['die_position'] for c in cams if c['die_position']})
            if die_positions:
                placeholders = ",".join("?" * len(die_positions))
                cursor = await db.execute(
                    f"""SELECT id, set_no, cam_no FROM cam_items
                        WHERE job_id = ? AND status_station = 'active' AND set_no IN (
                            SELECT DISTINCT set_no FROM cam_items
                            WHERE job_id = ? AND set_no != ? AND status_station = 'active'
                              AND die_position IN ({placeholders})
                        )
                        ORDER BY set_no, cam_no""",
                    [job_id, job_id, set_no] + die_positions
                )
                for row in await cursor.fetchall():
                    bumped.append(await _apply_move(
                        db, row['id'], 'sharpen', operator, notes='Auto-bumped by set move'
                    ))

        moved = []
        for cam in to_move:
            moved.append(await _apply_move(
                db, cam['id'], to_station, operator,
                material_removed=material_removed.get(cam['id'])
            ))

        if not moved and not bumped:
            raise ValueError(f"All CAMs in set {set_no} are already in {to_station}")

        await db.commit()
    except Exception:
        await db.rollback()
        raise

    return {
        "success": True,
        "job_id": job_id,
        "set_no": set_no,
        "to_station": to_station,
        "moved": moved,
        "bumped": bumped,
        "bumped_sets": sorted({b['set_no'] for b in bumped}),
        "skipped": len(cams) - len(to_move)
    }


async def undo_last_move(db, cam_item_id: int) -> Dict:
    """
    Undo the last move for a CAM item.

    Marks the most recent move as undone, reverts the cam's station and
    reverses the lifespan bookkeeping that move caused.
    """
    cursor = await db.execute(
        """SELECT id, from_station, to_station, moved_at, material_removed
           FROM moves
           WHERE cam_item_id = ? AND undone = 0
           ORDER BY moved_at DESC, id DESC
           LIMIT 1""",
        (cam_item_id,)
    )
    last_move = await cursor.fetchone()
    if not last_move:
        raise ValueError("No moves to undo for this CAM item")

    undone_from = last_move['from_station']
    undone_to = last_move['to_station']
    # Undoing the creation record leaves the tool in the cabinet
    revert_to = 'cabinet' if undone_from == 'new' else undone_from

    await db.execute("UPDATE moves SET undone = 1 WHERE id = ?", (last_move['id'],))
    await _set_station(db, cam_item_id, revert_to)

    # Lifespan reversal is best-effort: the station revert must not be blocked by it
    try:
        material = last_move['material_removed']
        if requires_material_removed(undone_from, undone_to) and material is not None:
            active_ls = await get_active_lifespan(db, cam_item_id)
            if active_ls:
                await db.execute(
                    "UPDATE tool_lifespans SET total_material_removed = ?, sharpen_count = ? WHERE id = ?",
                    (max(0, active_ls['total_material_removed'] - material),
                     max(0, active_ls['sharpen_count'] - 1),
                     active_ls['id'])
                )

        if undone_from == 'refill' and undone_to != 'refill':
            # Drop the (still empty) lifespan opened by leaving refill
            await db.execute(
                """DELETE FROM tool_lifespans
                   WHERE cam_item_id = ? AND ended_at IS NULL
                     AND sharpen_count = 0 AND total_material_removed = 0
                     AND lifespan_number = (
                         SELECT MAX(lifespan_number) FROM tool_lifespans
                         WHERE cam_item_id = ? AND ended_at IS NULL)""",
                (cam_item_id, cam_item_id)
            )

        if undone_to == 'refill' or undone_from == 'refill':
            await _reopen_latest_closed_lifespan(db, cam_item_id)
    except Exception as e:
        logger.error(f"Lifespan undo tracking failed for cam {cam_item_id}: {e}")

    await db.commit()

    return {
        "success": True,
        "cam_item_id": cam_item_id,
        "undone_move_id": last_move['id'],
        "reverted_to_station": revert_to,
        "original_move": {
            "from": undone_from,
            "to": undone_to,
            "moved_at": last_move['moved_at']
        }
    }


async def _reopen_latest_closed_lifespan(db, cam_item_id: int):
    await db.execute(
        """UPDATE tool_lifespans SET ended_at = NULL
           WHERE cam_item_id = ? AND ended_at IS NOT NULL
             AND lifespan_number = (
                 SELECT MAX(lifespan_number) FROM tool_lifespans
                 WHERE cam_item_id = ? AND ended_at IS NOT NULL)""",
        (cam_item_id, cam_item_id)
    )


# ========== Priority & Hot List ==========

def calculate_priority(available_sets: int, refill_count: int, priority_level: str,
                       all_in_sharpen: bool = False,
                       no_cabinet_with_active_set: bool = False,
                       has_blocked_position: bool = False) -> Tuple[int, int, str]:
    """
    Calculate job priority score based on multiple factors.

    Rules:
    - Base priority from admin setting: 0 (low) to 4 (top)
    - Priority score = base + adjustments:
      - If all_in_sharpen: +100 (massive boost to top of list)
      - If no cams in cabinet AND at least one active set: +2
      - If available_sets == 1: +1
      - If available_sets == 0: +2
      - If has_blocked_position (a cam_no has all instances in refill/sharpen): +100
      - If refill_count >= 3: +1

    Returns: (base_priority, priority_score, label)
    """
    base_priority = config.PRIORITY_VALUES.get(priority_level, 0)
    priority_score = base_priority

    if all_in_sharpen:
        priority_score += 100
    if no_cabinet_with_active_set:
        priority_score += 2
    if available_sets == 0:
        priority_score += 2
    elif available_sets == 1:
        priority_score += 1
    # A blocked position stops production, so it ranks like all_in_sharpen
    if has_blocked_position:
        priority_score += 100
    if refill_count >= 3:
        priority_score += 1

    label = config.PRIORITY_LABELS.get(priority_level, "Low")
    return base_priority, priority_score, label


_JOB_RANKING_SQL = """
    WITH job_stats AS (
        SELECT
            j.id,
            j.s_number,
            j.title,
            j.priority_level,
            COUNT(c.id) as total_count,
            COUNT(DISTINCT CASE WHEN c.status_station NOT IN ('refill', 'sharpen') THEN c.set_no END) as available_sets,
            COUNT(CASE WHEN c.status_station = 'active' THEN 1 END) as active_count,
            COUNT(CASE WHEN c.status_station = 'sharpen' THEN 1 END) as sharpen_count,
            COUNT(CASE WHEN c.status_station = 'cabinet' THEN 1 END) as cabinet_count,
            COUNT(CASE WHEN c.status_station = 'refill' THEN 1 END) as refill_count,
            MIN(c.status_updated_at) as oldest_update
        FROM jobs j
        LEFT JOIN cam_items c ON j.id = c.job_id
        GROUP BY j.id
    ),
    complete_active_sets AS (
        SELECT DISTINCT job_id
        FROM cam_items
        GROUP BY job_id, set_no
        HAVING COUNT(*) = COUNT(CASE WHEN status_station = 'active' THEN 1 END)
           AND COUNT(*) > 0
    ),
    blocked_positions AS (
        SELECT DISTINCT job_id
        FROM cam_items
        GROUP BY job_id, cam_no
        HAVING COUNT(*) = COUNT(CASE WHEN status_station IN ('refill', 'sharpen') THEN 1 END)
           AND COUNT(*) > 0
    )
    SELECT
        js.*,
        CASE WHEN cas.job_id IS NOT NULL THEN 1 ELSE 0 END as has_complete_active_set,
        CASE WHEN bp.job_id IS NOT NULL THEN 1 ELSE 0 END as has_blocked_position
    FROM job_stats js
    LEFT JOIN complete_active_sets cas ON js.id = cas.job_id
    LEFT JOIN blocked_positions bp ON js.id = bp.job_id
"""

# Hot list only shows jobs with work waiting in sharpen and not fully stocked
_HOT_LIST_FILTER = """
    WHERE js.sharpen_count > 0
      AND NOT (js.total_count > 0 AND js.cabinet_count = js.total_count)
"""


def _rank_job(job: Dict) -> Dict:
    all_in_sharpen = job['total_count'] > 0 and job['sharpen_count'] == job['total_count']
    no_cabinet_with_active_set = (
        job['cabinet_count'] == 0 and
        job['active_count'] > 0 and
        job['has_complete_active_set'] == 1
    )
    has_blocked_position = job['has_blocked_position'] == 1

    base_priority, priority_score, label = calculate_priority(
        available_sets=job['available_sets'],
        refill_count=job['refill_count'],
        priority_level=job['priority_level'] or 'low',
        all_in_sharpen=all_in_sharpen,
        no_cabinet_with_active_set=no_cabinet_with_active_set,
        has_blocked_position=has_blocked_position
    )

    return {
        "job_id": job['id'],
        "s_number": job['s_number'],
        "title": job['title'],
        "priority_level": job['priority_level'],
        "base_priority": base_priority,
        "priority_score": priority_score,
        "priority_label": label,
        "all_in_sharpen": all_in_sharpen,
        "has_blocked_position": has_blocked_position,
        "available_sets": job['available_sets'],
        "total_count": job['total_count'],
        "active_count": job['active_count'],
        "sharpen_count": job['sharpen_count'],
        "cabinet_count": job['cabinet_count'],
        "refill_count": job['refill_count'],
        "oldest_update": job['oldest_update']
    }


async def _rank_jobs(db, hot_only: bool) -> List[Dict]:
    sql = _JOB_RANKING_SQL + (_HOT_LIST_FILTER if hot_only else "")
    cursor = await db.execute(sql)
    ranked = [_rank_job(dict(row)) for row in await cursor.fetchall()]
    ranked.sort(key=lambda x: (-x['priority_score'], -x['base_priority']))
    return ranked


async def generate_hot_list(db) -> List[Dict]:
    """
    Priority queue of jobs that need sharpening attention.

    Only jobs with at least one cam in sharpen are shown, and jobs whose cams
    are all in the cabinet are hidden. Sorted by priority score (descending).
    """
    return await _rank_jobs(db, hot_only=True)


async def generate_all_jobs_ranked(db) -> List[Dict]:
    """Every job ranked with the hot list scoring (used for Top 5 selection)."""
    return await _rank_jobs(db, hot_only=False)


# ========== Tool Lifespan Management ==========

async def _cam_max_life(db, cam_item_id: int) -> float:
    cursor = await db.execute(
        "SELECT COALESCE(max_material_life, ?) as life FROM cam_items WHERE id = ?",
        (config.DEFAULT_MATERIAL_LIFE, cam_item_id)
    )
    cam = await cursor.fetchone()
    return cam['life'] if cam else config.DEFAULT_MATERIAL_LIFE


async def _next_lifespan_number(db, cam_item_id: int) -> int:
    cursor = await db.execute(
        "SELECT COALESCE(MAX(lifespan_number), 0) + 1 as next_num FROM tool_lifespans WHERE cam_item_id = ?",
        (cam_item_id,)
    )
    return (await cursor.fetchone())['next_num']


async def get_active_lifespan(db, cam_item_id: int) -> Optional[Dict]:
    """Get the current active lifespan for a tool (ended_at IS NULL)."""
    cursor = await db.execute(
        """SELECT id, cam_item_id, lifespan_number, started_at, ended_at,
                  total_material_removed, sharpen_count, max_material_life
           FROM tool_lifespans
           WHERE cam_item_id = ? AND ended_at IS NULL
           ORDER BY lifespan_number DESC LIMIT 1""",
        (cam_item_id,)
    )
    row = await cursor.fetchone()
    return dict(row) if row else None


async def update_lifespan_on_sharpen(db, cam_item_id: int, material_removed: float):
    """Update active lifespan when a sharpen->cabinet move occurs."""
    lifespan = await get_active_lifespan(db, cam_item_id)
    if lifespan:
        await db.execute(
            """UPDATE tool_lifespans
               SET total_material_removed = total_material_removed + ?,
                   sharpen_count = sharpen_count + 1
               WHERE id = ?""",
            (material_removed, lifespan['id'])
        )
        return

    # Edge case: no active lifespan exists, so start one with this sharpening
    await db.execute(
        """INSERT INTO tool_lifespans
           (cam_item_id, lifespan_number, total_material_removed, sharpen_count, max_material_life)
           VALUES (?, ?, ?, 1, ?)""",
        (cam_item_id, await _next_lifespan_number(db, cam_item_id), material_removed,
         await _cam_max_life(db, cam_item_id))
    )


async def close_lifespan_on_refill(db, cam_item_id: int):
    """Close the active lifespan when a tool moves to refill."""
    await db.execute(
        "UPDATE tool_lifespans SET ended_at = CURRENT_TIMESTAMP WHERE cam_item_id = ? AND ended_at IS NULL",
        (cam_item_id,)
    )


async def open_new_lifespan(db, cam_item_id: int):
    """Open a new lifespan when a tool returns from refill."""
    await db.execute(
        "INSERT INTO tool_lifespans (cam_item_id, lifespan_number, max_material_life) VALUES (?, ?, ?)",
        (cam_item_id, await _next_lifespan_number(db, cam_item_id), await _cam_max_life(db, cam_item_id))
    )


async def get_lifespan_forecast(db, cam_item_id: int) -> Dict:
    """
    Lifespan tracking data and forecast for a CAM item: current lifespan,
    estimated sharpenings remaining, and the last 3 completed lifespans.
    """
    current = await get_active_lifespan(db, cam_item_id)
    default_life = await _cam_max_life(db, cam_item_id)

    cursor = await db.execute(
        """SELECT lifespan_number, total_material_removed, sharpen_count, started_at, ended_at
           FROM tool_lifespans
           WHERE cam_item_id = ? AND ended_at IS NOT NULL
           ORDER BY lifespan_number DESC
           LIMIT 3""",
        (cam_item_id,)
    )
    completed = [dict(r) for r in await cursor.fetchall()]

    cursor = await db.execute(
        "SELECT COUNT(*) as cnt FROM tool_lifespans WHERE cam_item_id = ? AND ended_at IS NOT NULL",
        (cam_item_id,)
    )
    completed_count = (await cursor.fetchone())['cnt']

    if current:
        max_life = current['max_material_life']
        total_removed = current['total_material_removed']
        current_info = {
            "lifespan_number": current['lifespan_number'],
            "total_removed": round(total_removed, 3),
            "sharpen_count": current['sharpen_count'],
            "material_remaining": round(max(0, max_life - total_removed), 3),
            "percent_life_used": round((total_removed / max_life) * 100, 1) if max_life > 0 else 0,
            "max_material_life": max_life
        }
    else:
        max_life = default_life
        current_info = {
            "lifespan_number": 0,
            "total_removed": 0.0,
            "sharpen_count": 0,
            "material_remaining": round(max_life, 3),
            "percent_life_used": 0.0,
            "max_material_life": max_life
        }

    # Average removal per sharpen: blend current lifespan (60%) with history (40%)
    current_avg = (current['total_material_removed'] / current['sharpen_count']
                   if current and current['sharpen_count'] > 0 else None)
    historical_avgs = [ls['total_material_removed'] / ls['sharpen_count']
                       for ls in completed if ls['sharpen_count'] > 0]
    hist_avg = sum(historical_avgs) / len(historical_avgs) if historical_avgs else None

    if current_avg is not None and hist_avg is not None:
        avg_per_sharpen = current_avg * 0.6 + hist_avg * 0.4
    elif current_avg is not None:
        avg_per_sharpen = current_avg
    elif hist_avg is not None:
        avg_per_sharpen = hist_avg
    else:
        avg_per_sharpen = max_life / 25.0  # No data at all: rough estimate

    est_remaining = (floor(current_info['material_remaining'] / avg_per_sharpen)
                     if avg_per_sharpen > 0 else None)

    # Historical averages, padding to 3 lifespans with default-life assumptions
    hist_sharpen_counts = [ls['sharpen_count'] for ls in completed if ls['sharpen_count'] > 0]
    hist_total_removed = [ls['total_material_removed'] for ls in completed if ls['sharpen_count'] > 0]
    assumed_count = round(default_life / avg_per_sharpen) if avg_per_sharpen > 0 else 25
    while len(hist_sharpen_counts) < 3:
        hist_sharpen_counts.append(assumed_count)
        hist_total_removed.append(default_life)

    return {
        "current_lifespan": current_info,
        "forecast": {
            "avg_removal_per_sharpen": round(avg_per_sharpen, 4) if avg_per_sharpen else None,
            "estimated_sharpenings_remaining": est_remaining,
            "at_risk": est_remaining is not None and est_remaining <= 2
        },
        "history": {
            "completed_lifespans": completed_count,
            "last_3": completed,
            "avg_sharpenings_per_life": round(sum(hist_sharpen_counts) / len(hist_sharpen_counts), 1),
            "avg_total_removed_per_life": round(sum(hist_total_removed) / len(hist_total_removed), 3)
        }
    }


async def get_tool_stats(db, job_id: Optional[int] = None, cam_item_id: Optional[int] = None,
                         only_sharpened: bool = False, limit: Optional[int] = None) -> List[Dict]:
    """
    Per-tool sharpening and current-lifespan stats in a single query.

    Replaces per-tool ``sharpen-stats`` + ``lifespan`` round trips. Filter by
    job, or rank the most-sharpened tools shop-wide (``only_sharpened``).
    """
    conditions, params = [], []
    if job_id is not None:
        conditions.append("c.job_id = ?")
        params.append(job_id)
    if cam_item_id is not None:
        conditions.append("c.id = ?")
        params.append(cam_item_id)
    if only_sharpened:
        conditions.append("s.sharpen_count > 0")
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    order = ("ORDER BY s.sharpen_count DESC, j.s_number, c.set_no, c.cam_no" if only_sharpened
             else "ORDER BY c.set_no, c.cam_no")
    limit_sql = ""
    if limit is not None:
        limit_sql = "LIMIT ?"
        params.append(limit)

    cursor = await db.execute(
        f"""
        WITH sharpen AS (
            SELECT cam_item_id,
                   COUNT(*) AS sharpen_count,
                   AVG(material_removed) AS avg_material_removed,
                   MAX(moved_at) AS last_sharpen_date
            FROM moves
            WHERE from_station = 'sharpen' AND to_station = 'cabinet'
              AND undone = 0 AND material_removed IS NOT NULL
            GROUP BY cam_item_id
        )
        SELECT c.id AS cam_item_id, c.set_no, c.cam_no, c.status_station, j.s_number,
               COALESCE(s.sharpen_count, 0) AS sharpen_count,
               s.avg_material_removed,
               s.last_sharpen_date,
               (SELECT m.material_removed FROM moves m
                WHERE m.cam_item_id = c.id AND m.from_station = 'sharpen' AND m.to_station = 'cabinet'
                  AND m.undone = 0 AND m.material_removed IS NOT NULL
                ORDER BY m.moved_at DESC, m.id DESC LIMIT 1) AS last_material_removed,
               tl.lifespan_number,
               COALESCE(tl.total_material_removed, 0) AS lifespan_material_removed,
               COALESCE(tl.sharpen_count, 0) AS lifespan_sharpen_count,
               COALESCE(tl.max_material_life, c.max_material_life, ?) AS max_material_life
        FROM cam_items c
        JOIN jobs j ON j.id = c.job_id
        LEFT JOIN sharpen s ON s.cam_item_id = c.id
        LEFT JOIN tool_lifespans tl ON tl.cam_item_id = c.id AND tl.ended_at IS NULL
        {where}
        {order}
        {limit_sql}
        """,
        [config.DEFAULT_MATERIAL_LIFE] + params
    )

    stats = []
    for row in await cursor.fetchall():
        item = dict(row)
        max_life = item['max_material_life'] or 0
        removed = item['lifespan_material_removed']
        item['avg_material_removed'] = (round(item['avg_material_removed'], 3)
                                        if item['avg_material_removed'] is not None else None)
        item['material_remaining'] = round(max(0, max_life - removed), 3)
        item['percent_life_used'] = round(removed / max_life * 100, 1) if max_life > 0 else 0
        stats.append(item)
    return stats
