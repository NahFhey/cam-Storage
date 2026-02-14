"""
Manual entry parser for CAM tool identification
Supports multiple formats:
- S1793 SET1 CAM2
- 1793-1-2
- S1793-C2-SET1
- 1793 1 2
- etc.
"""
import re
from typing import Optional, Dict, List, Tuple
from dataclasses import dataclass

@dataclass
class ParsedEntry:
    """Result of parsing a manual entry"""
    s_number: str  # Without 'S' prefix
    set_no: Optional[int] = None
    cam_no: Optional[int] = None
    confidence: str = "exact"  # exact, partial, ambiguous

def parse_manual_entry(entry: str) -> ParsedEntry:
    """
    Parse a manual tool entry string into components.

    Returns ParsedEntry with:
    - s_number: always present (without 'S' prefix)
    - set_no: optional
    - cam_no: optional
    - confidence: exact (all parts), partial (missing set/cam), ambiguous
    """
    # Normalize: lowercase, strip, collapse whitespace
    normalized = entry.strip().lower()
    normalized = re.sub(r'\s+', ' ', normalized)

    # Remove common separators for easier parsing
    # Keep original for some patterns
    cleaned = normalized.replace('-', ' ').replace('_', ' ').replace('/', ' ')
    cleaned = re.sub(r'[^\w\s]', ' ', cleaned)  # Remove other punctuation
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()

    # Pattern 1: "S1793 SET1 CAM2" or "1793 SET 1 CAM 2"
    pattern1 = r's?(\d+)\s+set\s*(\d+)\s+cam\s*(\d+)'
    match = re.search(pattern1, cleaned)
    if match:
        return ParsedEntry(
            s_number=match.group(1),
            set_no=int(match.group(2)),
            cam_no=int(match.group(3)),
            confidence="exact"
        )

    # Pattern 2: "S1793-C2-SET1" or "1793 C2 SET1"
    pattern2 = r's?(\d+)\s+c\s*(\d+)\s+set\s*(\d+)'
    match = re.search(pattern2, cleaned)
    if match:
        return ParsedEntry(
            s_number=match.group(1),
            set_no=int(match.group(3)),
            cam_no=int(match.group(2)),
            confidence="exact"
        )

    # Pattern 3: "1793-1-2" or "1793 1 2" (S# SET CAM)
    # Try with dashes first
    pattern3_dash = r's?(\d+)[- ](\d+)[- ](\d+)'
    match = re.search(pattern3_dash, normalized)
    if match:
        return ParsedEntry(
            s_number=match.group(1),
            set_no=int(match.group(2)),
            cam_no=int(match.group(3)),
            confidence="exact"
        )

    # Pattern 4: Just S-number with SET and/or CAM mentioned
    pattern4 = r's?(\d+)\s+set\s*(\d+)'
    match = re.search(pattern4, cleaned)
    if match:
        return ParsedEntry(
            s_number=match.group(1),
            set_no=int(match.group(2)),
            confidence="partial"
        )

    pattern5 = r's?(\d+)\s+cam\s*(\d+)'
    match = re.search(pattern5, cleaned)
    if match:
        return ParsedEntry(
            s_number=match.group(1),
            cam_no=int(match.group(2)),
            confidence="partial"
        )

    # Pattern 6: Just S-number
    pattern6 = r's?(\d+)'
    match = re.search(pattern6, cleaned)
    if match:
        return ParsedEntry(
            s_number=match.group(1),
            confidence="partial"
        )

    # Unable to parse
    raise ValueError(f"Unable to parse entry: {entry}")

async def resolve_entry(db, entry: str) -> Dict:
    """
    Resolve a manual entry to a specific CAM item or list of candidates.

    Returns:
    {
        "status": "exact" | "multiple" | "not_found" | "job_not_found",
        "cam_item": {...} or None,
        "candidates": [...] or None,
        "job": {...} or None,
        "parsed": ParsedEntry
    }
    """
    parsed = parse_manual_entry(entry)
    parsed_dict = {
        "s_number": parsed.s_number,
        "set_no": parsed.set_no,
        "cam_no": parsed.cam_no,
        "confidence": parsed.confidence
    }

    # Look up job by S-number (match with or without S prefix)
    cursor = await db.execute(
        "SELECT id, s_number, title, priority_level, created_at, notes FROM jobs WHERE s_number = ? OR s_number = ? OR s_number = ?",
        (parsed.s_number, f"S{parsed.s_number}", parsed.s_number.lstrip("S"))
    )
    job = await cursor.fetchone()

    if not job:
        return {
            "status": "job_not_found",
            "parsed": parsed_dict,
            "s_number": parsed.s_number
        }

    job_dict = dict(job)

    # If we have set and cam, try exact match
    if parsed.set_no is not None and parsed.cam_no is not None:
        cursor = await db.execute(
            """
            SELECT id, job_id, set_no, cam_no, die_position, enter_die_steel, exit_die_steel,
                   status_station, status_updated_at, notes, eol_cycles_expected, max_material_life, created_at
            FROM cam_items
            WHERE job_id = ? AND set_no = ? AND cam_no = ?
            """,
            (job['id'], parsed.set_no, parsed.cam_no)
        )
        cam_item = await cursor.fetchone()

        if cam_item:
            return {
                "status": "exact",
                "cam_item": dict(cam_item),
                "job": job_dict,
                "parsed": parsed_dict
            }
        else:
            return {
                "status": "not_found",
                "job": job_dict,
                "parsed": parsed_dict,
                "message": f"CAM item not found: S{parsed.s_number} Set{parsed.set_no} Cam{parsed.cam_no}"
            }

    # If we have only set, return all cams in that set
    if parsed.set_no is not None:
        cursor = await db.execute(
            """
            SELECT id, job_id, set_no, cam_no, die_position, enter_die_steel, exit_die_steel,
                   status_station, status_updated_at, notes, eol_cycles_expected, max_material_life, created_at
            FROM cam_items
            WHERE job_id = ? AND set_no = ?
            ORDER BY cam_no
            """,
            (job['id'], parsed.set_no)
        )
        candidates = await cursor.fetchall()

        if candidates:
            return {
                "status": "multiple",
                "candidates": [dict(c) for c in candidates],
                "job": job_dict,
                "parsed": parsed_dict,
                "message": f"Multiple CAMs found in Set {parsed.set_no}"
            }
        else:
            return {
                "status": "not_found",
                "job": job_dict,
                "parsed": parsed_dict,
                "message": f"No CAMs found in Set {parsed.set_no}"
            }

    # If we have only cam number (rare), search across all sets
    if parsed.cam_no is not None:
        cursor = await db.execute(
            """
            SELECT id, job_id, set_no, cam_no, die_position, enter_die_steel, exit_die_steel,
                   status_station, status_updated_at, notes, eol_cycles_expected, max_material_life, created_at
            FROM cam_items
            WHERE job_id = ? AND cam_no = ?
            ORDER BY set_no
            """,
            (job['id'], parsed.cam_no)
        )
        candidates = await cursor.fetchall()

        if len(candidates) == 1:
            return {
                "status": "exact",
                "cam_item": dict(candidates[0]),
                "job": job_dict,
                "parsed": parsed_dict
            }
        elif len(candidates) > 1:
            return {
                "status": "multiple",
                "candidates": [dict(c) for c in candidates],
                "job": job_dict,
                "parsed": parsed_dict,
                "message": f"Multiple sets have Cam {parsed.cam_no}"
            }

    # Just S-number, return all cam items for this job
    cursor = await db.execute(
        """
        SELECT id, job_id, set_no, cam_no, die_position, enter_die_steel, exit_die_steel,
               status_station, status_updated_at, notes, eol_cycles_expected, max_material_life, created_at
        FROM cam_items
        WHERE job_id = ?
        ORDER BY set_no, cam_no
        """,
        (job['id'],)
    )
    candidates = await cursor.fetchall()

    if candidates:
        return {
            "status": "multiple",
            "candidates": [dict(c) for c in candidates],
            "job": job_dict,
            "parsed": parsed_dict,
            "message": f"Multiple CAMs found for S{parsed.s_number}"
        }
    else:
        return {
            "status": "not_found",
            "job": job_dict,
            "parsed": parsed_dict,
            "message": f"No CAMs found for S{parsed.s_number}"
        }
