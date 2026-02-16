"""
Business logic for CAM tracking operations:
- Station moves with auto-bump
- Undo functionality
- Priority calculation
- Hot list generation
"""
import logging
from datetime import datetime
from typing import Dict, List, Optional, Tuple
import config
from database import get_config_value

logger = logging.getLogger(__name__)

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
    Move a CAM item to a new station.

    If auto_bump is enabled and moving to 'active', will automatically
    move any existing active cam from the same job+set to 'sharpen'.

    Returns:
    {
        "success": True,
        "cam_item_id": int,
        "move_id": int,
        "from_station": str,
        "to_station": str,
        "auto_bumped": {...} or None
    }
    """
    # Validate station
    if to_station not in config.STATIONS:
        raise ValueError(f"Invalid station: {to_station}")

    # Get current cam item state
    cursor = await db.execute(
        "SELECT id, job_id, set_no, cam_no, die_position, status_station FROM cam_items WHERE id = ?",
        (cam_item_id,)
    )
    cam_item = await cursor.fetchone()

    if not cam_item:
        raise ValueError(f"CAM item not found: {cam_item_id}")

    from_station = cam_item['status_station']

    if from_station == to_station:
        raise ValueError(f"CAM is already in {to_station}")

    # Check if auto-bump is needed
    auto_bumped = None
    if auto_bump is None:
        auto_bump_str = await get_config_value('auto_bump_enabled', 'false', db=db)
        auto_bump = auto_bump_str.lower() == 'true'

    if auto_bump and to_station == 'active':
        # Find any other cam from DIFFERENT set but same die_position currently in active
        # Multiple cams from the SAME set can be active simultaneously
        # Only bump if it's a different set with matching die position
        if cam_item['die_position']:
            cursor = await db.execute(
                """
                SELECT id, set_no, cam_no FROM cam_items
                WHERE job_id = ?
                  AND set_no != ?
                  AND die_position = ?
                  AND status_station = 'active'
                  AND id != ?
                """,
                (cam_item['job_id'], cam_item['set_no'], cam_item['die_position'], cam_item_id)
            )
            existing_active = await cursor.fetchone()
        else:
            # If die_position not set, don't auto-bump
            existing_active = None

        if existing_active:
            # Auto-bump existing active cam to sharpen
            await db.execute(
                """
                UPDATE cam_items
                SET status_station = 'sharpen',
                    status_updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (existing_active['id'],)
            )

            # Record the auto-bump move
            cursor = await db.execute(
                """
                INSERT INTO moves (cam_item_id, from_station, to_station, operator, notes)
                VALUES (?, 'active', 'sharpen', ?, 'Auto-bumped by system')
                """,
                (existing_active['id'], operator or config.DEFAULT_OPERATOR)
            )
            auto_bump_move_id = cursor.lastrowid

            auto_bumped = {
                "cam_item_id": existing_active['id'],
                "move_id": auto_bump_move_id,
                "from_station": "active",
                "to_station": "sharpen",
                "set_no": existing_active['set_no'],
                "cam_no": existing_active['cam_no']
            }

    # Update cam item status
    await db.execute(
        """
        UPDATE cam_items
        SET status_station = ?,
            status_updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (to_station, cam_item_id)
    )

    # Record the move
    cursor = await db.execute(
        """
        INSERT INTO moves (cam_item_id, from_station, to_station, operator, notes, material_removed)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (cam_item_id, from_station, to_station, operator or config.DEFAULT_OPERATOR, notes, material_removed)
    )
    move_id = cursor.lastrowid

    # Lifespan tracking — atomic with the move (rolled back together on failure)
    try:
        if from_station == 'sharpen' and to_station == 'cabinet' and material_removed:
            await update_lifespan_on_sharpen(db, cam_item_id, material_removed)
        if to_station == 'refill':
            await close_lifespan_on_refill(db, cam_item_id)
        if from_station == 'refill' and to_station != 'refill':
            await open_new_lifespan(db, cam_item_id)
    except Exception as e:
        await db.rollback()
        logger.error(f"Move+lifespan transaction failed for cam {cam_item_id}: {e}")
        raise ValueError(f"Move failed due to lifespan tracking error: {e}")

    await db.commit()

    return {
        "success": True,
        "cam_item_id": cam_item_id,
        "move_id": move_id,
        "from_station": from_station,
        "to_station": to_station,
        "auto_bumped": auto_bumped
    }

async def undo_last_move(db, cam_item_id: int) -> Dict:
    """
    Undo the last move for a CAM item.

    Marks the most recent move as undone and reverts the cam_item status.

    Returns:
    {
        "success": True,
        "cam_item_id": int,
        "undone_move_id": int,
        "reverted_to_station": str
    }
    """
    # Get the most recent non-undone move for this cam
    cursor = await db.execute(
        """
        SELECT id, cam_item_id, from_station, to_station, moved_at, operator, notes, material_removed, undone
        FROM moves
        WHERE cam_item_id = ? AND undone = 0
        ORDER BY moved_at DESC, id DESC
        LIMIT 1
        """,
        (cam_item_id,)
    )
    last_move = await cursor.fetchone()

    if not last_move:
        raise ValueError("No moves to undo for this CAM item")

    # Mark move as undone
    await db.execute(
        "UPDATE moves SET undone = 1 WHERE id = ?",
        (last_move['id'],)
    )

    # Revert cam_item to previous station
    revert_to = last_move['from_station']
    if revert_to == 'new':
        # Special case: if undoing the first move, revert to cabinet as default
        revert_to = 'cabinet'

    await db.execute(
        """
        UPDATE cam_items
        SET status_station = ?,
            status_updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (revert_to, cam_item_id)
    )

    # Reverse lifespan changes for the undone move (non-blocking)
    try:
        undone_from = last_move['from_station']
        undone_to = last_move['to_station']
        undone_material = last_move['material_removed']

        # Undo sharpen->cabinet: decrement lifespan totals
        if undone_from == 'sharpen' and undone_to == 'cabinet' and undone_material:
            active_ls = await get_active_lifespan(db, cam_item_id)
            if active_ls:
                new_total = max(0, active_ls['total_material_removed'] - undone_material)
                new_count = max(0, active_ls['sharpen_count'] - 1)
                await db.execute(
                    "UPDATE tool_lifespans SET total_material_removed = ?, sharpen_count = ? WHERE id = ?",
                    (new_total, new_count, active_ls['id'])
                )

        # Undo move-to-refill: reopen the closed lifespan
        if undone_to == 'refill':
            await db.execute(
                """UPDATE tool_lifespans SET ended_at = NULL
                   WHERE cam_item_id = ? AND ended_at IS NOT NULL
                   AND lifespan_number = (
                       SELECT MAX(lifespan_number) FROM tool_lifespans
                       WHERE cam_item_id = ? AND ended_at IS NOT NULL
                   )""",
                (cam_item_id, cam_item_id)
            )

        # Undo move-from-refill: delete the newly opened lifespan, reopen previous
        if undone_from == 'refill' and undone_to != 'refill':
            # Delete the lifespan that was just opened (should have 0 sharpenings)
            await db.execute(
                """DELETE FROM tool_lifespans
                   WHERE cam_item_id = ? AND ended_at IS NULL
                   AND sharpen_count = 0 AND total_material_removed = 0
                   AND lifespan_number = (
                       SELECT MAX(lifespan_number) FROM tool_lifespans
                       WHERE cam_item_id = ? AND ended_at IS NULL
                   )""",
                (cam_item_id, cam_item_id)
            )
            # Reopen the previous completed lifespan
            await db.execute(
                """UPDATE tool_lifespans SET ended_at = NULL
                   WHERE cam_item_id = ? AND ended_at IS NOT NULL
                   AND lifespan_number = (
                       SELECT MAX(lifespan_number) FROM tool_lifespans
                       WHERE cam_item_id = ? AND ended_at IS NOT NULL
                   )""",
                (cam_item_id, cam_item_id)
            )
    except Exception as e:
        logger.error(f"Lifespan undo tracking failed for cam {cam_item_id}: {e}")

    await db.commit()

    return {
        "success": True,
        "cam_item_id": cam_item_id,
        "undone_move_id": last_move['id'],
        "reverted_to_station": revert_to,
        "original_move": {
            "from": last_move['from_station'],
            "to": last_move['to_station'],
            "moved_at": last_move['moved_at']
        }
    }

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
    # Base priority from admin setting
    base_priority = config.PRIORITY_VALUES.get(priority_level, 0)

    # Start with base for score calculation
    priority_score = base_priority

    # All in sharpen gets massive boost (goes to top of hot list)
    if all_in_sharpen:
        priority_score += 100

    # No cams in cabinet but has at least one active set (urgent situation)
    if no_cabinet_with_active_set:
        priority_score += 2

    # Available sets adjustment
    if available_sets == 0:
        priority_score += 2
    elif available_sets == 1:
        priority_score += 1

    # Blocked position: a cam_no has no available instance across any set
    # Being blocked stops production - massive boost like all_in_sharpen
    if has_blocked_position:
        priority_score += 100

    # Refill count adjustment
    if refill_count >= 3:
        priority_score += 1

    # Label is based on base priority level
    label = config.PRIORITY_LABELS.get(priority_level, "Low")

    return base_priority, priority_score, label

async def generate_hot_list(db) -> List[Dict]:
    """
    Generate the hot list (priority queue) for all jobs.

    Filtering rules:
    - Only show jobs with at least one cam in sharpen
    - Hide jobs where all cams are in cabinet

    Sorting:
    - By priority_score (descending)
    - Jobs with all cams in sharpen get huge boost

    Each job includes:
    - job info
    - computed priority score and label
    - counts by station
    - available sets count
    - oldest cam update timestamp
    """
    # Single CTE-based query: combines job stats and complete-active-set detection
    cursor = await db.execute("""
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
        WHERE js.sharpen_count > 0
          AND NOT (js.total_count > 0 AND js.cabinet_count = js.total_count)
    """)
    jobs = await cursor.fetchall()

    hot_list = []
    for job in jobs:
        job_dict = dict(job)

        # Check if ALL cams are in sharpen
        all_in_sharpen = (job_dict['total_count'] > 0 and
                         job_dict['sharpen_count'] == job_dict['total_count'])

        # Check if no cams in cabinet AND at least one complete active set
        no_cabinet_with_active_set = (
            job_dict['cabinet_count'] == 0 and
            job_dict['active_count'] > 0 and
            job_dict['has_complete_active_set'] == 1
        )

        # Check if any cam_no has all instances blocked (in refill/sharpen)
        has_blocked_position = job_dict['has_blocked_position'] == 1

        # Calculate priority
        base_priority, priority_score, label = calculate_priority(
            available_sets=job_dict['available_sets'],
            refill_count=job_dict['refill_count'],
            priority_level=job_dict['priority_level'] or 'low',
            all_in_sharpen=all_in_sharpen,
            no_cabinet_with_active_set=no_cabinet_with_active_set,
            has_blocked_position=has_blocked_position
        )

        hot_list.append({
            "job_id": job_dict['id'],
            "s_number": job_dict['s_number'],
            "title": job_dict['title'],
            "priority_level": job_dict['priority_level'],
            "base_priority": base_priority,
            "priority_score": priority_score,
            "priority_label": label,
            "all_in_sharpen": all_in_sharpen,
            "has_blocked_position": has_blocked_position,
            "available_sets": job_dict['available_sets'],
            "active_count": job_dict['active_count'],
            "sharpen_count": job_dict['sharpen_count'],
            "cabinet_count": job_dict['cabinet_count'],
            "refill_count": job_dict['refill_count'],
            "oldest_update": job_dict['oldest_update']
        })

    # Sort by priority_score (descending)
    hot_list.sort(key=lambda x: (-x['priority_score'], -x['base_priority']))

    return hot_list


# ========== Tool Lifespan Management ==========

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
    else:
        # Edge case: no active lifespan exists. Create one.
        cursor = await db.execute(
            "SELECT COALESCE(MAX(lifespan_number), 0) + 1 as next_num FROM tool_lifespans WHERE cam_item_id = ?",
            (cam_item_id,)
        )
        row = await cursor.fetchone()
        next_num = row['next_num']

        cursor = await db.execute(
            "SELECT COALESCE(max_material_life, 0.375) as life FROM cam_items WHERE id = ?",
            (cam_item_id,)
        )
        cam = await cursor.fetchone()
        max_life = cam['life'] if cam else 0.375

        await db.execute(
            """INSERT INTO tool_lifespans
               (cam_item_id, lifespan_number, total_material_removed, sharpen_count, max_material_life)
               VALUES (?, ?, ?, 1, ?)""",
            (cam_item_id, next_num, material_removed, max_life)
        )


async def close_lifespan_on_refill(db, cam_item_id: int):
    """Close the active lifespan when a tool moves to refill."""
    await db.execute(
        """UPDATE tool_lifespans
           SET ended_at = CURRENT_TIMESTAMP
           WHERE cam_item_id = ? AND ended_at IS NULL""",
        (cam_item_id,)
    )


async def open_new_lifespan(db, cam_item_id: int):
    """Open a new lifespan when a tool returns from refill."""
    cursor = await db.execute(
        "SELECT COALESCE(MAX(lifespan_number), 0) + 1 as next_num FROM tool_lifespans WHERE cam_item_id = ?",
        (cam_item_id,)
    )
    row = await cursor.fetchone()
    next_num = row['next_num']

    cursor = await db.execute(
        "SELECT COALESCE(max_material_life, 0.375) as life FROM cam_items WHERE id = ?",
        (cam_item_id,)
    )
    cam = await cursor.fetchone()
    max_life = cam['life'] if cam else 0.375

    await db.execute(
        """INSERT INTO tool_lifespans
           (cam_item_id, lifespan_number, max_material_life)
           VALUES (?, ?, ?)""",
        (cam_item_id, next_num, max_life)
    )


async def get_lifespan_forecast(db, cam_item_id: int) -> Dict:
    """
    Get lifespan tracking data and forecast for a CAM item.

    Returns current lifespan info, forecast of sharpenings remaining,
    and history of last 3 completed lifespans.
    """
    from math import floor

    # Get active lifespan
    current = await get_active_lifespan(db, cam_item_id)

    # Get cam item for max_material_life default
    cursor = await db.execute(
        "SELECT COALESCE(max_material_life, 0.375) as life FROM cam_items WHERE id = ?",
        (cam_item_id,)
    )
    cam = await cursor.fetchone()
    default_life = cam['life'] if cam else 0.375

    # Get last 3 completed lifespans
    cursor = await db.execute(
        """SELECT lifespan_number, total_material_removed, sharpen_count, started_at, ended_at
           FROM tool_lifespans
           WHERE cam_item_id = ? AND ended_at IS NOT NULL
           ORDER BY lifespan_number DESC
           LIMIT 3""",
        (cam_item_id,)
    )
    completed = [dict(r) for r in await cursor.fetchall()]

    # Count total completed lifespans
    cursor = await db.execute(
        "SELECT COUNT(*) as cnt FROM tool_lifespans WHERE cam_item_id = ? AND ended_at IS NOT NULL",
        (cam_item_id,)
    )
    completed_count = (await cursor.fetchone())['cnt']

    # Build current lifespan info
    if current:
        max_life = current['max_material_life']
        total_removed = current['total_material_removed']
        material_remaining = max(0, max_life - total_removed)
        percent_used = round((total_removed / max_life) * 100, 1) if max_life > 0 else 0

        current_info = {
            "lifespan_number": current['lifespan_number'],
            "total_removed": round(total_removed, 3),
            "sharpen_count": current['sharpen_count'],
            "material_remaining": round(material_remaining, 3),
            "percent_life_used": percent_used,
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

    # Calculate average removal per sharpen
    avg_per_sharpen = None

    # First try current lifespan
    if current and current['sharpen_count'] > 0:
        avg_per_sharpen = current['total_material_removed'] / current['sharpen_count']

    # Blend with historical data if available
    historical_avgs = []
    for ls in completed:
        if ls['sharpen_count'] > 0:
            historical_avgs.append(ls['total_material_removed'] / ls['sharpen_count'])

    if avg_per_sharpen is not None and historical_avgs:
        # Blend current with historical
        hist_avg = sum(historical_avgs) / len(historical_avgs)
        avg_per_sharpen = (avg_per_sharpen * 0.6 + hist_avg * 0.4)
    elif avg_per_sharpen is None and historical_avgs:
        avg_per_sharpen = sum(historical_avgs) / len(historical_avgs)
    elif avg_per_sharpen is None:
        # No data at all — rough estimate
        avg_per_sharpen = max_life / 25.0

    # Estimate sharpenings remaining
    material_remaining = current_info['material_remaining']
    if avg_per_sharpen > 0:
        est_remaining = floor(material_remaining / avg_per_sharpen)
    else:
        est_remaining = None

    at_risk = est_remaining is not None and est_remaining <= 2

    # Calculate historical averages (fill missing slots with 0.375 assumption)
    hist_sharpen_counts = [ls['sharpen_count'] for ls in completed if ls['sharpen_count'] > 0]
    hist_total_removed = [ls['total_material_removed'] for ls in completed if ls['sharpen_count'] > 0]

    # Fill to 3 slots with defaults if fewer than 3 completed
    while len(hist_sharpen_counts) < 3:
        # Assume default life with the current avg per sharpen
        if avg_per_sharpen > 0:
            assumed_count = round(default_life / avg_per_sharpen)
        else:
            assumed_count = 25
        hist_sharpen_counts.append(assumed_count)
        hist_total_removed.append(default_life)

    avg_sharpenings = round(sum(hist_sharpen_counts) / len(hist_sharpen_counts), 1)
    avg_total = round(sum(hist_total_removed) / len(hist_total_removed), 3)

    return {
        "current_lifespan": current_info,
        "forecast": {
            "avg_removal_per_sharpen": round(avg_per_sharpen, 4) if avg_per_sharpen else None,
            "estimated_sharpenings_remaining": est_remaining,
            "at_risk": at_risk
        },
        "history": {
            "completed_lifespans": completed_count,
            "last_3": completed,
            "avg_sharpenings_per_life": avg_sharpenings,
            "avg_total_removed_per_life": avg_total
        }
    }
