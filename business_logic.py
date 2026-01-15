"""
Business logic for CAM tracking operations:
- Station moves with auto-bump
- Undo functionality
- Priority calculation
- Hot list generation
"""
from datetime import datetime
from typing import Dict, List, Optional, Tuple
import config
from database import get_config_value

async def move_cam_to_station(
    db,
    cam_item_id: int,
    to_station: str,
    operator: str = None,
    notes: str = None,
    auto_bump: bool = None
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
        "SELECT * FROM cam_items WHERE id = ?",
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
        auto_bump_str = await get_config_value('auto_bump_enabled', 'false')
        auto_bump = auto_bump_str.lower() == 'true'

    if auto_bump and to_station == 'active':
        # Find any other cam from same job+set currently in active
        cursor = await db.execute(
            """
            SELECT * FROM cam_items
            WHERE job_id = ? AND set_no = ? AND status_station = 'active' AND id != ?
            """,
            (cam_item['job_id'], cam_item['set_no'], cam_item_id)
        )
        existing_active = await cursor.fetchone()

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
        INSERT INTO moves (cam_item_id, from_station, to_station, operator, notes)
        VALUES (?, ?, ?, ?, ?)
        """,
        (cam_item_id, from_station, to_station, operator or config.DEFAULT_OPERATOR, notes)
    )
    move_id = cursor.lastrowid

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
        SELECT * FROM moves
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

def calculate_priority(available_sets: int, refill_count: int, priority_base: int) -> Tuple[int, str]:
    """
    Calculate job priority based on available sets and refill count.

    Rules:
    - Start with priority_base
    - If available_sets == 1: +1
    - If available_sets == 0: +3
    - Add +1 for each cam in refill

    Returns: (numeric_priority, label)
    """
    priority = priority_base

    # Available sets adjustment
    if available_sets == 0:
        priority += 3
    elif available_sets == 1:
        priority += 1

    # Refill count adjustment
    priority += refill_count

    # Get label
    if priority >= 3:
        label = "Urgent"
    elif priority == 2:
        label = "High"
    elif priority == 1:
        label = "Medium"
    else:
        label = "Low"

    return priority, label

async def generate_hot_list(db) -> List[Dict]:
    """
    Generate the hot list (priority queue) for all jobs.

    Returns list of jobs sorted by:
    1. Priority (desc)
    2. Refill count (desc)
    3. Oldest update (asc)

    Each job includes:
    - job info
    - computed priority and label
    - counts by station
    - available sets count
    - oldest cam update timestamp
    """
    # Get all jobs with their cam statistics
    cursor = await db.execute("""
        SELECT
            j.id,
            j.s_number,
            j.title,
            j.priority_base,
            COUNT(DISTINCT CASE WHEN c.status_station != 'refill' THEN c.set_no END) as available_sets,
            COUNT(CASE WHEN c.status_station = 'active' THEN 1 END) as active_count,
            COUNT(CASE WHEN c.status_station = 'sharpen' THEN 1 END) as sharpen_count,
            COUNT(CASE WHEN c.status_station = 'cabinet' THEN 1 END) as cabinet_count,
            COUNT(CASE WHEN c.status_station = 'refill' THEN 1 END) as refill_count,
            MIN(c.status_updated_at) as oldest_update
        FROM jobs j
        LEFT JOIN cam_items c ON j.id = c.job_id
        GROUP BY j.id
    """)
    jobs = await cursor.fetchall()

    hot_list = []
    for job in jobs:
        job_dict = dict(job)

        # Calculate priority
        priority, label = calculate_priority(
            available_sets=job_dict['available_sets'],
            refill_count=job_dict['refill_count'],
            priority_base=job_dict['priority_base']
        )

        hot_list.append({
            "job_id": job_dict['id'],
            "s_number": job_dict['s_number'],
            "title": job_dict['title'],
            "priority_base": job_dict['priority_base'],
            "priority": priority,
            "priority_label": label,
            "available_sets": job_dict['available_sets'],
            "active_count": job_dict['active_count'],
            "sharpen_count": job_dict['sharpen_count'],
            "cabinet_count": job_dict['cabinet_count'],
            "refill_count": job_dict['refill_count'],
            "oldest_update": job_dict['oldest_update']
        })

    # Sort by priority (desc), refill_count (desc), oldest_update (asc)
    hot_list.sort(
        key=lambda x: (-x['priority'], -x['refill_count'], x['oldest_update'] or '9999')
    )

    return hot_list
