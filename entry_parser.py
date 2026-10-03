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
from dataclasses import asdict, dataclass
from typing import Dict, Optional

from business_logic import CAM_ITEM_COLUMNS

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

async def _find_cams(db, job_id: int, extra_where: str = "", params: tuple = (),
                     order: str = "set_no, cam_no") -> list:
    cursor = await db.execute(
        f"SELECT {CAM_ITEM_COLUMNS} FROM cam_items WHERE job_id = ? {extra_where} ORDER BY {order}",
        (job_id,) + params
    )
    return [dict(row) for row in await cursor.fetchall()]


async def resolve_entry(db, entry: str) -> Dict:
    """
    Resolve a manual entry to a specific CAM item or list of candidates.

    Returns:
    {
        "status": "exact" | "multiple" | "not_found" | "job_not_found",
        "cam_item": {...} (exact),
        "candidates": [...] (multiple),
        "job": {...},
        "parsed": {s_number, set_no, cam_no, confidence},
        "message": str (multiple / not_found)
    }
    """
    parsed = parse_manual_entry(entry)
    parsed_dict = asdict(parsed)

    cursor = await db.execute(
        "SELECT id, s_number, title, priority_level, created_at, notes FROM jobs WHERE s_number = ? OR s_number = ?",
        (parsed.s_number, f"S{parsed.s_number}")
    )
    job = await cursor.fetchone()
    if not job:
        return {"status": "job_not_found", "parsed": parsed_dict, "s_number": parsed.s_number}

    job_dict = dict(job)
    base = {"job": job_dict, "parsed": parsed_dict}

    def exact(cam):
        return {"status": "exact", "cam_item": cam, **base}

    def multiple(cams, message):
        return {"status": "multiple", "candidates": cams, "message": message, **base}

    def not_found(message):
        return {"status": "not_found", "message": message, **base}

    if parsed.set_no is not None and parsed.cam_no is not None:
        cams = await _find_cams(db, job['id'], "AND set_no = ? AND cam_no = ?", (parsed.set_no, parsed.cam_no))
        if cams:
            return exact(cams[0])
        return not_found(f"CAM item not found: S{parsed.s_number} Set{parsed.set_no} Cam{parsed.cam_no}")

    if parsed.set_no is not None:
        cams = await _find_cams(db, job['id'], "AND set_no = ?", (parsed.set_no,), order="cam_no")
        if cams:
            return multiple(cams, f"Multiple CAMs found in Set {parsed.set_no}")
        return not_found(f"No CAMs found in Set {parsed.set_no}")

    if parsed.cam_no is not None:
        cams = await _find_cams(db, job['id'], "AND cam_no = ?", (parsed.cam_no,), order="set_no")
        if len(cams) == 1:
            return exact(cams[0])
        if cams:
            return multiple(cams, f"Multiple sets have Cam {parsed.cam_no}")
        # No such cam number: fall back to listing every tool in the job

    cams = await _find_cams(db, job['id'])
    if cams:
        return multiple(cams, f"Multiple CAMs found for S{parsed.s_number}")
    return not_found(f"No CAMs found for S{parsed.s_number}")
