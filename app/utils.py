"""Query helpers shared by the routers."""
import csv
import io
from typing import Callable, Dict, Iterable, List, Optional, Sequence

from fastapi import HTTPException
from fastapi.responses import StreamingResponse

from business_logic import CAM_ITEM_COLUMNS
from database import get_db_connection

JOB_COLUMNS = "id, s_number, title, priority_level, created_at, notes"
MOVE_COLUMNS = "id, cam_item_id, from_station, to_station, moved_at, operator, notes, material_removed, undone"
USER_COLUMNS = "id, username, display_name, role, active, created_at, updated_at"

__all__ = [
    "CAM_ITEM_COLUMNS", "JOB_COLUMNS", "MOVE_COLUMNS", "USER_COLUMNS",
    "fetch_one", "fetch_all", "require_one", "update_row", "record_priority_change", "csv_response",
]


async def fetch_one(db, sql: str, params: Sequence = ()) -> Optional[Dict]:
    cursor = await db.execute(sql, params)
    row = await cursor.fetchone()
    return dict(row) if row else None


async def fetch_all(db, sql: str, params: Sequence = ()) -> List[Dict]:
    cursor = await db.execute(sql, params)
    return [dict(row) for row in await cursor.fetchall()]


async def require_one(db, sql: str, params: Sequence, not_found: str) -> Dict:
    row = await fetch_one(db, sql, params)
    if row is None:
        raise HTTPException(status_code=404, detail=not_found)
    return row


async def update_row(db, table: str, row_id: int, fields: Dict, touch: Optional[str] = None):
    """UPDATE the given columns of one row.

    ``fields`` keys must come from a validated request model (never raw input);
    ``touch`` names a timestamp column to set to CURRENT_TIMESTAMP.
    """
    if not fields:
        raise HTTPException(status_code=400, detail="No fields to update")
    assignments = [f"{column} = ?" for column in fields]
    if touch:
        assignments.append(f"{touch} = CURRENT_TIMESTAMP")
    await db.execute(
        f"UPDATE {table} SET {', '.join(assignments)} WHERE id = ?",
        list(fields.values()) + [row_id]
    )


async def record_priority_change(db, job_id: int, user: dict, old_priority: str,
                                 new_priority: str, reason: str, source: str):
    """Update a job's priority and append the change to the audit log (caller commits)."""
    await db.execute("UPDATE jobs SET priority_level = ? WHERE id = ?", (new_priority, job_id))
    await db.execute(
        """INSERT INTO priority_changes
           (job_id, changed_by_user_id, changed_by_username, old_priority, new_priority, reason, source)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (job_id, user['user_id'], user['username'], old_priority, new_priority, reason, source)
    )


def _csv_chunk(rows: Iterable[Sequence]) -> str:
    output = io.StringIO()
    csv.writer(output).writerows(rows)
    return output.getvalue()


def csv_response(filename: str, header: List[str], sql: str,
                 row: Callable[[Dict], Sequence], params: Sequence = ()) -> StreamingResponse:
    """Stream a query result as a CSV download.

    The generator opens its own connection: the request-scoped one from
    ``get_db`` is released before the response body is streamed.
    """
    async def generate():
        yield _csv_chunk([header])
        async with get_db_connection() as db:
            cursor = await db.execute(sql, params)
            while True:
                rows = await cursor.fetchmany(500)
                if not rows:
                    break
                yield _csv_chunk(row(dict(r)) for r in rows)

    return StreamingResponse(
        generate(),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )
