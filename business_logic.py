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
        # Find any other cam from DIFFERENT set but same die_position currently in active
        # Multiple cams from the SAME set can be active simultaneously
        # Only bump if it's a different set with matching die position
        if cam_item['die_position']:
            cursor = await db.execute(
                """
                SELECT * FROM cam_items
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

def calculate_priority(available_sets: int, refill_count: int, priority_level: str,
                       all_in_sharpen: bool = False,
                       no_cabinet_with_active_set: bool = False) -> Tuple[int, int, str]:
    """
    Calculate job priority score based on multiple factors.

    Rules:
    - Base priority from admin setting: 0 (low) to 4 (top)
    - Priority score = base + adjustments:
      - If all_in_sharpen: +100 (massive boost to top of list)
      - If no cams in cabinet AND at least one active set: +2
      - If available_sets == 1: +1
      - If available_sets == 0: +2
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
    # Get all jobs with their cam statistics
    cursor = await db.execute("""
        SELECT
            j.id,
            j.s_number,
            j.title,
            j.priority_level,
            COUNT(c.id) as total_count,
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

        # Skip if no cams in sharpen
        if job_dict['sharpen_count'] == 0:
            continue

        # Skip if all cams are in cabinet (no work needed)
        if job_dict['total_count'] > 0 and job_dict['cabinet_count'] == job_dict['total_count']:
            continue

        # Check if ALL cams are in sharpen
        all_in_sharpen = (job_dict['total_count'] > 0 and
                         job_dict['sharpen_count'] == job_dict['total_count'])

        # Check if no cams in cabinet AND at least one complete active set
        no_cabinet_with_active_set = False
        if job_dict['cabinet_count'] == 0 and job_dict['active_count'] > 0:
            # Check if there's at least one set where ALL cams are active
            cursor = await db.execute("""
                SELECT set_no, COUNT(*) as set_size,
                       COUNT(CASE WHEN status_station = 'active' THEN 1 END) as active_in_set
                FROM cam_items
                WHERE job_id = ?
                GROUP BY set_no
                HAVING set_size = active_in_set AND active_in_set > 0
            """, (job_dict['id'],))
            active_sets = await cursor.fetchall()
            no_cabinet_with_active_set = len(active_sets) > 0

        # Calculate priority
        base_priority, priority_score, label = calculate_priority(
            available_sets=job_dict['available_sets'],
            refill_count=job_dict['refill_count'],
            priority_level=job_dict['priority_level'] or 'low',
            all_in_sharpen=all_in_sharpen,
            no_cabinet_with_active_set=no_cabinet_with_active_set
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
            "available_sets": job_dict['available_sets'],
            "active_count": job_dict['active_count'],
            "sharpen_count": job_dict['sharpen_count'],
            "cabinet_count": job_dict['cabinet_count'],
            "refill_count": job_dict['refill_count'],
            "oldest_update": job_dict['oldest_update']
        })

    # Sort by priority_score (descending)
    hot_list.sort(key=lambda x: -x['priority_score'])

    return hot_list
