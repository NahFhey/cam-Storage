"""
FastAPI backend for CAM Tracking Kiosk
"""
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Depends, Response, UploadFile, File, status, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field, field_validator
from typing import Optional, List, Dict
from datetime import datetime, timedelta
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
import aiosqlite
import csv
import io
import shutil
import logging
import sys
import secrets
import os

import config
from database import get_db, get_config_value, set_config_value, init_database, migrate_database, hash_pin, verify_pin
from entry_parser import parse_manual_entry, resolve_entry
from business_logic import move_cam_to_station, undo_last_move, generate_hot_list

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('cam_tracking.log'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# Lifespan context manager for startup/shutdown
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize and migrate the database on startup."""
    init_database()
    migrate_database()
    yield

# Initialize FastAPI app
app = FastAPI(title="CAM Tracking Kiosk API", version="1.0.0", lifespan=lifespan)

# Rate limiting setup
limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# ========== Session Management ==========

# In-memory session store: token -> user session dict
active_sessions: Dict[str, dict] = {}


def create_session(user: dict) -> str:
    """Create a new session for a user and return the token"""
    token = secrets.token_urlsafe(32)
    active_sessions[token] = {
        "user_id": user["id"],
        "username": user["username"],
        "display_name": user["display_name"],
        "role": user["role"],
        "expires_at": (datetime.now() + timedelta(hours=config.SESSION_DURATION_HOURS)).isoformat()
    }
    return token


def get_session(token: str) -> Optional[dict]:
    """Get a valid session by token, or None if expired/missing"""
    session = active_sessions.get(token)
    if not session:
        return None
    if datetime.fromisoformat(session["expires_at"]) <= datetime.now():
        del active_sessions[token]
        return None
    return session


def cleanup_sessions():
    """Remove expired sessions"""
    now = datetime.now()
    expired = [t for t, s in active_sessions.items()
               if datetime.fromisoformat(s["expires_at"]) <= now]
    for t in expired:
        del active_sessions[t]


# ========== Auth Dependencies ==========

async def get_current_user(request: Request) -> dict:
    """Get the current authenticated user from the session token."""
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated"
        )
    token = auth_header[7:]
    session = get_session(token)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session expired or invalid"
        )
    return session


async def get_admin_user(user: dict = Depends(get_current_user)) -> dict:
    """Require admin role."""
    if user["role"] != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required"
        )
    return user


async def get_optional_user(request: Request) -> Optional[dict]:
    """Get the current user if authenticated, None otherwise."""
    try:
        return await get_current_user(request)
    except HTTPException:
        return None

# Serve static files (frontend)
app.mount("/static", StaticFiles(directory="static"), name="static")

# Pydantic models for request/response with validation
class JobCreate(BaseModel):
    s_number: str = Field(..., min_length=1, max_length=50, description="Job S-number (e.g., 'S1793')")
    title: Optional[str] = Field(None, max_length=200, description="Job title or description")
    priority_level: str = Field("low", description="Priority level: low, medium, high, urgent, top")
    notes: Optional[str] = Field(None, max_length=1000, description="Additional notes")

    @field_validator('s_number')
    @classmethod
    def validate_s_number(cls, v):
        """Ensure S-number follows expected format, store as digits only"""
        if not v:
            raise ValueError('S-number cannot be empty')
        # Strip S prefix and store just the digits
        cleaned = v.upper().strip().lstrip('S')
        if not cleaned.isdigit():
            raise ValueError('S-number must be numeric (e.g., S1793 or 1793)')
        return cleaned

    @field_validator('priority_level')
    @classmethod
    def validate_priority(cls, v):
        """Validate priority level"""
        if v not in config.PRIORITY_LEVELS:
            raise ValueError(f'Priority must be one of: {", ".join(config.PRIORITY_LEVELS)}')
        return v

class JobUpdate(BaseModel):
    title: Optional[str] = Field(None, max_length=200)
    priority_level: Optional[str] = None
    notes: Optional[str] = Field(None, max_length=1000)

    @field_validator('priority_level')
    @classmethod
    def validate_priority(cls, v):
        """Validate priority level"""
        if v is not None and v not in config.PRIORITY_LEVELS:
            raise ValueError(f'Priority must be one of: {", ".join(config.PRIORITY_LEVELS)}')
        return v

class CamItemCreate(BaseModel):
    job_id: int = Field(..., gt=0, description="Job ID")
    set_no: int = Field(..., gt=0, le=999, description="Set number (1-999)")
    cam_no: int = Field(..., gt=0, le=999, description="CAM number (1-999)")
    die_position: Optional[str] = Field(None, description="Die position: upper or lower")
    enter_die_steel: Optional[str] = Field(None, max_length=50)
    exit_die_steel: Optional[str] = Field(None, max_length=50)
    status_station: str = Field("cabinet", description="Initial station")
    notes: Optional[str] = Field(None, max_length=1000)
    eol_cycles_expected: Optional[int] = Field(None, ge=0, description="Expected end-of-life cycles")

    @field_validator('die_position')
    @classmethod
    def validate_die_position(cls, v):
        """Validate die position"""
        if v is not None and v not in config.DIE_POSITIONS:
            raise ValueError(f'Die position must be one of: {", ".join(config.DIE_POSITIONS)}')
        return v

    @field_validator('status_station')
    @classmethod
    def validate_station(cls, v):
        """Validate station"""
        if v not in config.STATIONS:
            raise ValueError(f'Station must be one of: {", ".join(config.STATIONS)}')
        return v

class CamItemUpdate(BaseModel):
    die_position: Optional[str] = None
    enter_die_steel: Optional[str] = Field(None, max_length=50)
    exit_die_steel: Optional[str] = Field(None, max_length=50)
    notes: Optional[str] = Field(None, max_length=1000)
    eol_cycles_expected: Optional[int] = Field(None, ge=0)

    @field_validator('die_position')
    @classmethod
    def validate_die_position(cls, v):
        """Validate die position"""
        if v is not None and v not in config.DIE_POSITIONS:
            raise ValueError(f'Die position must be one of: {", ".join(config.DIE_POSITIONS)}')
        return v

class CamConfig(BaseModel):
    cam_no: int = Field(..., gt=0, le=999, description="CAM number")
    die_position: Optional[str] = Field(None, description="Die position: upper or lower")
    enter_die_steel: Optional[str] = Field(None, max_length=50)
    exit_die_steel: Optional[str] = Field(None, max_length=50)

    @field_validator('die_position')
    @classmethod
    def validate_die_position(cls, v):
        if v is not None and v not in config.DIE_POSITIONS:
            raise ValueError(f'Die position must be one of: {", ".join(config.DIE_POSITIONS)}')
        return v

class CamItemBulkCreate(BaseModel):
    job_id: int = Field(..., gt=0)
    num_sets: int = Field(..., gt=0, le=100, description="Number of sets to create (1..num_sets)")
    cams: List[CamConfig] = Field(..., min_length=1, max_length=100, description="CAM configurations to replicate per set")

    @field_validator('cams')
    @classmethod
    def validate_cams(cls, v):
        """Validate CAM numbers are unique"""
        cam_nos = [c.cam_no for c in v]
        if len(cam_nos) != len(set(cam_nos)):
            raise ValueError('CAM numbers must be unique')
        return v

class MoveRequest(BaseModel):
    cam_item_id: int = Field(..., gt=0)
    to_station: str = Field(..., description="Destination station")
    operator: Optional[str] = Field(None, max_length=100)
    notes: Optional[str] = Field(None, max_length=500)
    auto_bump: Optional[bool] = None
    material_removed: Optional[float] = Field(None, ge=0.0, le=1.0, description="Material removed in inches (0-1)")

    @field_validator('to_station')
    @classmethod
    def validate_station(cls, v):
        """Validate station"""
        if v not in config.STATIONS:
            raise ValueError(f'Station must be one of: {", ".join(config.STATIONS)}')
        return v

class EntryResolveRequest(BaseModel):
    entry: str = Field(..., min_length=1, max_length=100, description="Manual entry string")

class ConfigUpdate(BaseModel):
    auto_bump_enabled: Optional[bool] = None

class LoginRequest(BaseModel):
    pin: str = Field(..., min_length=1, max_length=20, description="User PIN")

class UserCreate(BaseModel):
    username: str = Field(..., min_length=1, max_length=50, description="Unique username")
    display_name: str = Field(..., min_length=1, max_length=100, description="Display name")
    pin: str = Field(..., min_length=4, max_length=20, description="Login PIN (min 4 characters)")
    role: str = Field("user", description="User role: user or admin")

    @field_validator('role')
    @classmethod
    def validate_role(cls, v):
        if v not in config.USER_ROLES:
            raise ValueError(f'Role must be one of: {", ".join(config.USER_ROLES)}')
        return v

    @field_validator('username')
    @classmethod
    def validate_username(cls, v):
        if not v.replace('_', '').replace('-', '').isalnum():
            raise ValueError('Username must be alphanumeric (underscores and hyphens allowed)')
        return v.lower()

class UserUpdate(BaseModel):
    display_name: Optional[str] = Field(None, min_length=1, max_length=100)
    pin: Optional[str] = Field(None, min_length=4, max_length=20)
    role: Optional[str] = None
    active: Optional[bool] = None

    @field_validator('role')
    @classmethod
    def validate_role(cls, v):
        if v is not None and v not in config.USER_ROLES:
            raise ValueError(f'Role must be one of: {", ".join(config.USER_ROLES)}')
        return v

# Root endpoint - serve main page
@app.get("/")
async def root():
    return FileResponse("static/index.html")

# Health check endpoint
@app.get("/health")
async def health_check(db: aiosqlite.Connection = Depends(get_db)):
    """
    Health check endpoint for monitoring.

    Returns service status and database connectivity.
    """
    try:
        # Check disk space
        stats = shutil.disk_usage(os.path.dirname(config.DATABASE_PATH))
        free_gb = stats.free / (1024**3)
        total_gb = stats.total / (1024**3)

        # Get all database stats in a single query
        cursor = await db.execute("""
            SELECT
                (SELECT COUNT(*) FROM jobs) as jobs_count,
                (SELECT COUNT(*) FROM cam_items) as cams_count,
                (SELECT COUNT(*) FROM moves WHERE undone = 0) as moves_count
        """)
        row = await cursor.fetchone()
        jobs_count = row['jobs_count']
        cams_count = row['cams_count']
        moves_count = row['moves_count']

        return {
            "status": "healthy",
            "timestamp": datetime.now().isoformat(),
            "database": {
                "status": "connected",
                "jobs_count": jobs_count,
                "cams_count": cams_count,
                "moves_count": moves_count
            },
            "disk": {
                "free_gb": round(free_gb, 2),
                "total_gb": round(total_gb, 2),
                "used_percent": round((1 - free_gb / total_gb) * 100, 1)
            },
            "version": "1.0.0"
        }
    except Exception as e:
        logger.error(f"Health check failed: {str(e)}")
        return Response(
            content='{"status":"unhealthy"}',
            status_code=503,
            media_type="application/json"
        )

# ========== Auth Endpoints ==========

@app.post("/api/auth/login")
@limiter.limit("20/minute")
async def login(request: Request, login_req: LoginRequest, db: aiosqlite.Connection = Depends(get_db)):
    """Log in by PIN only. The system matches the PIN to a user."""
    cleanup_sessions()

    cursor = await db.execute(
        "SELECT id, username, display_name, pin_hash, role FROM users WHERE active = 1"
    )
    users = await cursor.fetchall()

    for user in users:
        if verify_pin(login_req.pin, user["pin_hash"]):
            user_dict = dict(user)
            token = create_session(user_dict)
            logger.info(f"User '{user_dict['username']}' logged in successfully")
            return {
                "token": token,
                "user": {
                    "id": user_dict["id"],
                    "username": user_dict["username"],
                    "display_name": user_dict["display_name"],
                    "role": user_dict["role"]
                }
            }

    logger.warning("Failed login attempt with invalid PIN")
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid PIN"
    )


@app.post("/api/auth/logout")
async def logout(request: Request):
    """Log out and invalidate the session."""
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
        if token in active_sessions:
            session = active_sessions.pop(token)
            logger.info(f"User '{session['username']}' logged out")
    return {"success": True}


@app.get("/api/auth/me")
async def get_me(user: dict = Depends(get_current_user)):
    """Get the current authenticated user's info."""
    return {
        "user_id": user["user_id"],
        "username": user["username"],
        "display_name": user["display_name"],
        "role": user["role"]
    }


# ========== User Management Endpoints ==========

@app.get("/api/users")
async def list_users(
    admin: dict = Depends(get_admin_user),
    db: aiosqlite.Connection = Depends(get_db)
):
    """List all users (admin only). Never returns PIN hashes."""
    cursor = await db.execute(
        "SELECT id, username, display_name, role, active, created_at, updated_at FROM users ORDER BY username"
    )
    users = await cursor.fetchall()
    return [dict(u) for u in users]


@app.post("/api/users")
async def create_user(
    user_data: UserCreate,
    admin: dict = Depends(get_admin_user),
    db: aiosqlite.Connection = Depends(get_db)
):
    """Create a new user (admin only)."""
    # Check that the PIN isn't already used by another active user
    cursor = await db.execute(
        "SELECT id, pin_hash FROM users WHERE active = 1"
    )
    existing = await cursor.fetchall()
    for u in existing:
        if verify_pin(user_data.pin, u["pin_hash"]):
            raise HTTPException(
                status_code=400,
                detail="This PIN is already in use by another user"
            )

    pin_hashed = hash_pin(user_data.pin)
    try:
        cursor = await db.execute(
            """INSERT INTO users (username, display_name, pin_hash, role)
               VALUES (?, ?, ?, ?)""",
            (user_data.username, user_data.display_name, pin_hashed, user_data.role)
        )
        await db.commit()
        user_id = cursor.lastrowid
        logger.info(f"Admin '{admin['username']}' created user '{user_data.username}' (role: {user_data.role})")

        return {
            "id": user_id,
            "username": user_data.username,
            "display_name": user_data.display_name,
            "role": user_data.role,
            "active": True
        }
    except aiosqlite.IntegrityError:
        raise HTTPException(status_code=400, detail=f"Username '{user_data.username}' already exists")


@app.patch("/api/users/{user_id}")
async def update_user(
    user_id: int,
    user_data: UserUpdate,
    admin: dict = Depends(get_admin_user),
    db: aiosqlite.Connection = Depends(get_db)
):
    """Update a user (admin only)."""
    # Verify user exists
    cursor = await db.execute("SELECT * FROM users WHERE id = ?", (user_id,))
    existing_user = await cursor.fetchone()
    if not existing_user:
        raise HTTPException(status_code=404, detail="User not found")

    update_parts = []
    values = []

    if user_data.display_name is not None:
        update_parts.append("display_name = ?")
        values.append(user_data.display_name)

    if user_data.role is not None:
        update_parts.append("role = ?")
        values.append(user_data.role)

    if user_data.active is not None:
        update_parts.append("active = ?")
        values.append(1 if user_data.active else 0)

    if user_data.pin is not None:
        # Check PIN uniqueness among other active users
        cursor = await db.execute(
            "SELECT id, pin_hash FROM users WHERE active = 1 AND id != ?", (user_id,)
        )
        others = await cursor.fetchall()
        for u in others:
            if verify_pin(user_data.pin, u["pin_hash"]):
                raise HTTPException(
                    status_code=400,
                    detail="This PIN is already in use by another user"
                )
        update_parts.append("pin_hash = ?")
        values.append(hash_pin(user_data.pin))

    if not update_parts:
        raise HTTPException(status_code=400, detail="No fields to update")

    update_parts.append("updated_at = CURRENT_TIMESTAMP")
    values.append(user_id)
    query = f"UPDATE users SET {', '.join(update_parts)} WHERE id = ?"
    await db.execute(query, values)
    await db.commit()

    # If deactivating, invalidate their sessions
    if user_data.active is False:
        tokens_to_remove = [
            t for t, s in active_sessions.items() if s["user_id"] == user_id
        ]
        for t in tokens_to_remove:
            del active_sessions[t]

    logger.info(f"Admin '{admin['username']}' updated user id={user_id}")

    cursor = await db.execute(
        "SELECT id, username, display_name, role, active, created_at, updated_at FROM users WHERE id = ?",
        (user_id,)
    )
    updated = await cursor.fetchone()
    return dict(updated)


@app.delete("/api/users/{user_id}")
async def delete_user(
    user_id: int,
    admin: dict = Depends(get_admin_user),
    db: aiosqlite.Connection = Depends(get_db)
):
    """Deactivate a user (admin only). Does not delete, just sets active=0."""
    # Prevent self-deactivation
    if admin["user_id"] == user_id:
        raise HTTPException(status_code=400, detail="Cannot deactivate your own account")

    cursor = await db.execute("SELECT username FROM users WHERE id = ?", (user_id,))
    user = await cursor.fetchone()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    await db.execute(
        "UPDATE users SET active = 0, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (user_id,)
    )
    await db.commit()

    # Invalidate their sessions
    tokens_to_remove = [
        t for t, s in active_sessions.items() if s["user_id"] == user_id
    ]
    for t in tokens_to_remove:
        del active_sessions[t]

    logger.info(f"Admin '{admin['username']}' deactivated user '{user['username']}'")
    return {"success": True, "message": f"User '{user['username']}' deactivated"}


# ========== Jobs Endpoints ==========

@app.get("/api/jobs")
@limiter.limit("100/minute")
async def list_jobs(
    request: Request,
    skip: int = 0,
    limit: int = 100,
    db: aiosqlite.Connection = Depends(get_db)
):
    """
    List jobs with pagination.

    Args:
        skip: Number of records to skip (default: 0)
        limit: Maximum number of records to return (default: 100, max: 500)

    Returns:
        Dictionary with items, total count, skip, and limit
    """
    # Enforce maximum limit
    limit = min(limit, 500)

    # Get total count
    cursor = await db.execute("SELECT COUNT(*) as count FROM jobs")
    total = (await cursor.fetchone())['count']

    # Get paginated results
    cursor = await db.execute(
        "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ? OFFSET ?",
        (limit, skip)
    )
    jobs = await cursor.fetchall()

    return {
        "items": [dict(job) for job in jobs],
        "total": total,
        "skip": skip,
        "limit": limit,
        "has_more": (skip + limit) < total
    }

@app.get("/api/jobs/{job_id}")
async def get_job(job_id: int, db: aiosqlite.Connection = Depends(get_db)):
    """Get a specific job with its cam items"""
    cursor = await db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
    job = await cursor.fetchone()

    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    # Get cam items for this job
    cursor = await db.execute(
        """
        SELECT * FROM cam_items
        WHERE job_id = ?
        ORDER BY set_no, cam_no
        """,
        (job_id,)
    )
    cam_items = await cursor.fetchall()

    return {
        "job": dict(job),
        "cam_items": [dict(c) for c in cam_items]
    }

@app.post("/api/jobs")
@limiter.limit("50/minute")
async def create_job(
    request: Request,
    job: JobCreate,
    db: aiosqlite.Connection = Depends(get_db),
    admin_user: dict = Depends(get_admin_user)
):
    """Create a new job (requires admin authentication)"""
    try:
        cursor = await db.execute(
            """
            INSERT INTO jobs (s_number, title, priority_level, notes)
            VALUES (?, ?, ?, ?)
            """,
            (job.s_number, job.title, job.priority_level, job.notes)
        )
        await db.commit()
        job_id = cursor.lastrowid

        cursor = await db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
        new_job = await cursor.fetchone()
        return dict(new_job)
    except aiosqlite.IntegrityError:
        raise HTTPException(status_code=400, detail=f"Job {job.s_number} already exists")

@app.patch("/api/jobs/{job_id}")
async def update_job(
    job_id: int,
    job: JobUpdate,
    db: aiosqlite.Connection = Depends(get_db),
    admin_user: dict = Depends(get_admin_user)
):
    """Update a job (requires admin authentication)"""
    # Verify job exists
    cursor = await db.execute("SELECT id FROM jobs WHERE id = ?", (job_id,))
    if not await cursor.fetchone():
        raise HTTPException(status_code=404, detail="Job not found")

    # Build update query safely with explicit field mapping
    update_parts = []
    values = []

    if job.title is not None:
        update_parts.append("title = ?")
        values.append(job.title)
    if job.priority_level is not None:
        update_parts.append("priority_level = ?")
        values.append(job.priority_level)
    if job.notes is not None:
        update_parts.append("notes = ?")
        values.append(job.notes)

    if not update_parts:
        raise HTTPException(status_code=400, detail="No fields to update")

    values.append(job_id)
    # Safe: update_parts only contains literal strings we control
    query = f"UPDATE jobs SET {', '.join(update_parts)} WHERE id = ?"
    await db.execute(query, values)
    await db.commit()

    cursor = await db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
    updated_job = await cursor.fetchone()
    return dict(updated_job)

@app.delete("/api/jobs/{job_id}")
async def delete_job(
    job_id: int,
    db: aiosqlite.Connection = Depends(get_db),
    admin_user: dict = Depends(get_admin_user)
):
    """Delete a job (requires admin authentication, cascades to cam items and moves)"""
    cursor = await db.execute("SELECT id FROM jobs WHERE id = ?", (job_id,))
    if not await cursor.fetchone():
        raise HTTPException(status_code=404, detail="Job not found")

    await db.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
    await db.commit()
    return {"success": True, "message": "Job deleted"}

# ========== CAM Items Endpoints ==========

@app.get("/api/cam-items")
@limiter.limit("150/minute")
async def list_cam_items(
    request: Request,
    job_id: Optional[int] = None,
    station: Optional[str] = None,
    skip: int = 0,
    limit: int = 100,
    db: aiosqlite.Connection = Depends(get_db)
):
    """
    List cam items with optional filters and pagination.

    Args:
        job_id: Filter by job ID
        station: Filter by station
        skip: Number of records to skip (default: 0)
        limit: Maximum number of records to return (default: 100, max: 500)
    """
    # Enforce maximum limit
    limit = min(limit, 500)

    # Build query
    where_clauses = ["1=1"]
    params = []

    if job_id:
        where_clauses.append("job_id = ?")
        params.append(job_id)

    if station:
        if station not in config.STATIONS:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid station. Must be one of: {', '.join(config.STATIONS)}"
            )
        where_clauses.append("status_station = ?")
        params.append(station)

    where_sql = " AND ".join(where_clauses)

    # Get total count
    count_query = f"SELECT COUNT(*) as count FROM cam_items WHERE {where_sql}"
    cursor = await db.execute(count_query, params)
    total = (await cursor.fetchone())['count']

    # Get paginated results
    query = f"SELECT * FROM cam_items WHERE {where_sql} ORDER BY status_updated_at DESC LIMIT ? OFFSET ?"
    cursor = await db.execute(query, params + [limit, skip])
    items = await cursor.fetchall()

    return {
        "items": [dict(item) for item in items],
        "total": total,
        "skip": skip,
        "limit": limit,
        "has_more": (skip + limit) < total,
        "filters": {"job_id": job_id, "station": station}
    }

@app.get("/api/cam-items/{cam_item_id}")
async def get_cam_item(cam_item_id: int, db: aiosqlite.Connection = Depends(get_db)):
    """Get a specific cam item with its move history"""
    cursor = await db.execute("SELECT * FROM cam_items WHERE id = ?", (cam_item_id,))
    cam_item = await cursor.fetchone()

    if not cam_item:
        raise HTTPException(status_code=404, detail="CAM item not found")

    # Get move history
    cursor = await db.execute(
        """
        SELECT * FROM moves
        WHERE cam_item_id = ?
        ORDER BY moved_at DESC
        """,
        (cam_item_id,)
    )
    moves = await cursor.fetchall()

    # Get job info
    cursor = await db.execute("SELECT * FROM jobs WHERE id = ?", (cam_item['job_id'],))
    job = await cursor.fetchone()

    return {
        "cam_item": dict(cam_item),
        "job": dict(job) if job else None,
        "moves": [dict(m) for m in moves]
    }

@app.post("/api/cam-items")
async def create_cam_item(
    item: CamItemCreate,
    db: aiosqlite.Connection = Depends(get_db),
    admin_user: dict = Depends(get_admin_user)
):
    """Create a single cam item (requires admin authentication)"""
    try:
        cursor = await db.execute(
            """
            INSERT INTO cam_items
            (job_id, set_no, cam_no, die_position, enter_die_steel, exit_die_steel,
             status_station, notes, eol_cycles_expected)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (item.job_id, item.set_no, item.cam_no, item.die_position, item.enter_die_steel,
             item.exit_die_steel, item.status_station, item.notes, item.eol_cycles_expected)
        )
        await db.commit()
        cam_item_id = cursor.lastrowid

        # Record initial move
        await db.execute(
            """
            INSERT INTO moves (cam_item_id, from_station, to_station, operator, notes)
            VALUES (?, 'new', ?, ?, 'Initial creation')
            """,
            (cam_item_id, item.status_station, config.DEFAULT_OPERATOR)
        )
        await db.commit()

        cursor = await db.execute("SELECT * FROM cam_items WHERE id = ?", (cam_item_id,))
        new_item = await cursor.fetchone()
        return dict(new_item)
    except aiosqlite.IntegrityError:
        raise HTTPException(
            status_code=400,
            detail=f"CAM item already exists: job_id={item.job_id}, set={item.set_no}, cam={item.cam_no}"
        )

@app.patch("/api/cam-items/{cam_item_id}")
async def update_cam_item(
    cam_item_id: int,
    item: CamItemUpdate,
    db: aiosqlite.Connection = Depends(get_db),
    admin_user: dict = Depends(get_admin_user)
):
    """Update a CAM item (requires admin authentication - die steel info, notes, etc.)"""
    # Verify item exists
    cursor = await db.execute("SELECT id FROM cam_items WHERE id = ?", (cam_item_id,))
    if not await cursor.fetchone():
        raise HTTPException(status_code=404, detail="CAM item not found")

    # Build update query safely with explicit field mapping
    update_parts = []
    values = []

    if item.die_position is not None:
        update_parts.append("die_position = ?")
        values.append(item.die_position)

    if item.enter_die_steel is not None:
        update_parts.append("enter_die_steel = ?")
        values.append(item.enter_die_steel)

    if item.exit_die_steel is not None:
        update_parts.append("exit_die_steel = ?")
        values.append(item.exit_die_steel)

    if item.notes is not None:
        update_parts.append("notes = ?")
        values.append(item.notes)

    if item.eol_cycles_expected is not None:
        update_parts.append("eol_cycles_expected = ?")
        values.append(item.eol_cycles_expected)

    if not update_parts:
        raise HTTPException(status_code=400, detail="No fields to update")

    values.append(cam_item_id)
    # Safe: update_parts only contains literal strings we control
    query = f"UPDATE cam_items SET {', '.join(update_parts)} WHERE id = ?"
    await db.execute(query, values)
    await db.commit()

    cursor = await db.execute("SELECT * FROM cam_items WHERE id = ?", (cam_item_id,))
    updated_item = await cursor.fetchone()
    return dict(updated_item)

@app.post("/api/cam-items/bulk")
async def create_cam_items_bulk(
    bulk: CamItemBulkCreate,
    db: aiosqlite.Connection = Depends(get_db),
    admin_user: dict = Depends(get_admin_user)
):
    """Create multiple cam items for a job (requires admin authentication - num_sets x configured CAMs)"""
    initial_station = "cabinet"

    # Build all rows to insert, using INSERT OR IGNORE to skip duplicates
    cam_rows = [
        (bulk.job_id, set_no, cam.cam_no, cam.die_position, cam.enter_die_steel, cam.exit_die_steel, initial_station)
        for set_no in range(1, bulk.num_sets + 1)
        for cam in bulk.cams
    ]

    await db.executemany(
        """INSERT OR IGNORE INTO cam_items (job_id, set_no, cam_no, die_position, enter_die_steel, exit_die_steel, status_station)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        cam_rows
    )

    # Fetch the IDs of all items we just created (or that already existed)
    sets_list = list(range(1, bulk.num_sets + 1))
    cam_nos = [cam.cam_no for cam in bulk.cams]
    set_placeholders = ",".join(["?"] * len(sets_list))
    cam_placeholders = ",".join(["?"] * len(cam_nos))
    cursor = await db.execute(
        f"""SELECT id, set_no, cam_no FROM cam_items
            WHERE job_id = ? AND set_no IN ({set_placeholders})
            AND cam_no IN ({cam_placeholders})
            ORDER BY set_no, cam_no""",
        [bulk.job_id] + sets_list + cam_nos
    )
    created_rows = await cursor.fetchall()

    # Record initial moves for items that don't already have one
    cam_ids = [row['id'] for row in created_rows]
    if cam_ids:
        id_placeholders = ",".join(["?"] * len(cam_ids))
        cursor = await db.execute(
            f"SELECT DISTINCT cam_item_id FROM moves WHERE cam_item_id IN ({id_placeholders})",
            cam_ids
        )
        existing_moves = {row['cam_item_id'] for row in await cursor.fetchall()}

        move_rows = [
            (row['id'], initial_station, config.DEFAULT_OPERATOR)
            for row in created_rows
            if row['id'] not in existing_moves
        ]
        if move_rows:
            await db.executemany(
                """INSERT INTO moves (cam_item_id, from_station, to_station, operator, notes)
                   VALUES (?, 'new', ?, ?, 'Bulk creation')""",
                move_rows
            )

    await db.commit()

    created_items = [
        {"id": row['id'], "set_no": row['set_no'], "cam_no": row['cam_no']}
        for row in created_rows
    ]

    return {
        "success": True,
        "created_count": len(created_items),
        "items": created_items
    }

# ========== Entry Resolution Endpoint ==========

@app.post("/api/resolve-entry")
@limiter.limit("200/minute")
async def resolve_entry_endpoint(
    request: Request,
    body: EntryResolveRequest,
    db: aiosqlite.Connection = Depends(get_db)
):
    """
    Resolve a manual entry string to CAM item(s).

    Returns:
    - status: exact | multiple | not_found | job_not_found
    - cam_item: if exact match
    - candidates: if multiple matches
    - job: job info
    """
    try:
        result = await resolve_entry(db, body.entry)
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

# ========== Move Operations Endpoints ==========

@app.post("/api/moves")
@limiter.limit("200/minute")
async def move_cam(
    request: Request,
    move: MoveRequest,
    db: aiosqlite.Connection = Depends(get_db),
    user: dict = Depends(get_current_user)
):
    """Move a cam item to a new station (requires login)"""
    try:
        # Get current cam state to check from_station
        cursor = await db.execute(
            "SELECT status_station FROM cam_items WHERE id = ?",
            (move.cam_item_id,)
        )
        cam = await cursor.fetchone()

        if not cam:
            raise HTTPException(status_code=404, detail="CAM item not found")

        # Validate material_removed when moving from sharpen to cabinet
        if cam['status_station'] == 'sharpen' and move.to_station == 'cabinet':
            if move.material_removed is None:
                raise HTTPException(
                    status_code=400,
                    detail="Material removed must be specified when moving from Sharpen to Cabinet"
                )
            if move.material_removed < 0 or move.material_removed > 1.0:
                raise HTTPException(
                    status_code=400,
                    detail="Material removed must be between 0.000 and 1.000 inches"
                )

        # Use the logged-in user's display name as operator
        operator = user["display_name"]

        result = await move_cam_to_station(
            db,
            cam_item_id=move.cam_item_id,
            to_station=move.to_station,
            operator=operator,
            notes=move.notes,
            auto_bump=move.auto_bump,
            material_removed=move.material_removed
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/api/moves/undo/{cam_item_id}")
async def undo_move(
    cam_item_id: int,
    db: aiosqlite.Connection = Depends(get_db),
    user: dict = Depends(get_current_user)
):
    """Undo the last move for a cam item (requires login)"""
    try:
        result = await undo_last_move(db, cam_item_id)
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/api/moves")
async def list_moves(
    cam_item_id: Optional[int] = None,
    limit: int = 100,
    db: aiosqlite.Connection = Depends(get_db)
):
    """List recent moves"""
    if cam_item_id:
        cursor = await db.execute(
            """
            SELECT * FROM moves
            WHERE cam_item_id = ?
            ORDER BY moved_at DESC
            LIMIT ?
            """,
            (cam_item_id, limit)
        )
    else:
        cursor = await db.execute(
            """
            SELECT m.*, c.job_id, c.set_no, c.cam_no
            FROM moves m
            JOIN cam_items c ON m.cam_item_id = c.id
            ORDER BY m.moved_at DESC
            LIMIT ?
            """,
            (limit,)
        )

    moves = await cursor.fetchall()
    return [dict(m) for m in moves]

@app.get("/api/cam-items/{cam_item_id}/sharpen-stats")
async def get_sharpen_stats(cam_item_id: int, db: aiosqlite.Connection = Depends(get_db)):
    """Get sharpening statistics for a CAM item"""
    # Get all sharpen stats in a single query using a window function
    cursor = await db.execute(
        """
        SELECT
            COUNT(*) as sharpen_count,
            AVG(material_removed) as avg_material_removed,
            MAX(moved_at) as last_sharpen_date,
            (
                SELECT material_removed FROM moves
                WHERE cam_item_id = ?
                  AND from_station = 'sharpen' AND to_station = 'cabinet'
                  AND undone = 0 AND material_removed IS NOT NULL
                ORDER BY moved_at DESC LIMIT 1
            ) as last_material_removed
        FROM moves
        WHERE cam_item_id = ?
          AND from_station = 'sharpen'
          AND to_station = 'cabinet'
          AND undone = 0
          AND material_removed IS NOT NULL
        """,
        (cam_item_id, cam_item_id)
    )
    stats = await cursor.fetchone()

    return {
        "sharpen_count": stats['sharpen_count'] or 0,
        "avg_material_removed": round(stats['avg_material_removed'], 3) if stats['avg_material_removed'] else None,
        "last_material_removed": stats['last_material_removed'],
        "last_sharpen_date": stats['last_sharpen_date']
    }

# ========== Hot List Endpoint ==========

@app.get("/api/hot-list")
async def get_hot_list(db: aiosqlite.Connection = Depends(get_db)):
    """Get the priority hot list"""
    hot_list = await generate_hot_list(db)
    return hot_list

# ========== Search Endpoint ==========

@app.get("/api/search")
@limiter.limit("100/minute")
async def search(
    request: Request,
    q: str,
    db: aiosqlite.Connection = Depends(get_db)
):
    """Search for jobs and cam items by S-number, set, cam, or keywords"""
    search_term = f"%{q}%"
    # Also search with S prefix stripped for S-number matching
    q_stripped = q.upper().strip().lstrip('S')
    search_stripped = f"%{q_stripped}%"

    # Search jobs (match against both raw input and stripped S-number)
    cursor = await db.execute(
        """
        SELECT * FROM jobs
        WHERE s_number LIKE ? OR s_number LIKE ? OR title LIKE ?
        ORDER BY s_number
        """,
        (search_term, search_stripped, search_term)
    )
    jobs = await cursor.fetchall()

    # Try to parse as entry and find cam items
    cam_items = []
    try:
        result = await resolve_entry(db, q)
        if result['status'] == 'exact':
            cam_items = [result['cam_item']]
        elif result['status'] == 'multiple':
            cam_items = result['candidates']
    except Exception as e:
        logger.debug(f"Entry parsing failed for search query '{q}': {e}")

    return {
        "query": q,
        "jobs": [dict(j) for j in jobs],
        "cam_items": cam_items
    }

# ========== Analytics Endpoints ==========

@app.get("/api/analytics/station-counts")
async def analytics_station_counts(db: aiosqlite.Connection = Depends(get_db)):
    """Get counts of tools by station"""
    cursor = await db.execute(
        """
        SELECT
            status_station as station,
            COUNT(*) as count
        FROM cam_items
        GROUP BY status_station
        ORDER BY
            CASE status_station
                WHEN 'active' THEN 1
                WHEN 'sharpen' THEN 2
                WHEN 'cabinet' THEN 3
                WHEN 'refill' THEN 4
            END
        """
    )
    counts = await cursor.fetchall()
    return [dict(c) for c in counts]

@app.get("/api/analytics/moves-recent")
async def analytics_moves_recent(days: int = 7, db: aiosqlite.Connection = Depends(get_db)):
    """Get move counts for recent days"""
    cursor = await db.execute(
        """
        SELECT
            DATE(moved_at) as date,
            COUNT(*) as count
        FROM moves
        WHERE moved_at >= datetime('now', '-' || ? || ' days')
        AND undone = 0
        GROUP BY DATE(moved_at)
        ORDER BY date DESC
        """,
        (days,)
    )
    moves = await cursor.fetchall()
    return [dict(m) for m in moves]

@app.get("/api/analytics/dwell-times")
async def analytics_dwell_times(db: aiosqlite.Connection = Depends(get_db)):
    """Get average dwell time per station (in hours)"""
    cursor = await db.execute(
        """
        SELECT
            c.status_station as station,
            AVG(
                (julianday('now') - julianday(c.status_updated_at)) * 24
            ) as avg_hours,
            COUNT(*) as count
        FROM cam_items c
        GROUP BY c.status_station
        """
    )
    times = await cursor.fetchall()
    return [dict(t) for t in times]

@app.get("/api/analytics/cycle-counts")
async def analytics_cycle_counts(limit: int = 20, db: aiosqlite.Connection = Depends(get_db)):
    """Get cycle counts (moves into 'active') per cam item"""
    cursor = await db.execute(
        """
        SELECT
            c.id as cam_item_id,
            j.s_number,
            c.set_no,
            c.cam_no,
            COUNT(m.id) as cycle_count
        FROM cam_items c
        JOIN jobs j ON c.job_id = j.id
        LEFT JOIN moves m ON c.id = m.cam_item_id AND m.to_station = 'active' AND m.undone = 0
        GROUP BY c.id
        ORDER BY cycle_count DESC
        LIMIT ?
        """,
        (limit,)
    )
    cycles = await cursor.fetchall()
    return [dict(c) for c in cycles]

@app.get("/api/analytics/sharpen-backlog")
async def analytics_sharpen_backlog(db: aiosqlite.Connection = Depends(get_db)):
    """Get sharpen backlog stats"""
    cursor = await db.execute(
        """
        SELECT
            COUNT(*) as backlog_size,
            AVG(
                (julianday('now') - julianday(status_updated_at)) * 24
            ) as avg_hours_in_sharpen
        FROM cam_items
        WHERE status_station = 'sharpen'
        """
    )
    stats = await cursor.fetchone()
    return dict(stats)

# ========== Configuration Endpoints ==========

@app.get("/api/config")
async def get_config():
    """Get current configuration"""
    auto_bump = await get_config_value('auto_bump_enabled', 'false')
    return {
        "auto_bump_enabled": auto_bump.lower() == 'true'
    }

@app.patch("/api/config")
async def update_config(
    config_update: ConfigUpdate,
    admin_user: dict = Depends(get_admin_user)
):
    """Update configuration (requires admin authentication)"""
    if config_update.auto_bump_enabled is not None:
        await set_config_value(
            'auto_bump_enabled',
            'true' if config_update.auto_bump_enabled else 'false'
        )

    return await get_config()

# ========== Export Endpoints ==========

@app.get("/api/export/jobs/csv")
async def export_jobs_csv(
    db: aiosqlite.Connection = Depends(get_db),
    user: dict = Depends(get_current_user)
):
    """Export jobs to CSV (requires authentication)"""
    async def generate():
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(['id', 's_number', 'title', 'priority_level', 'created_at', 'notes'])
        yield output.getvalue()

        cursor = await db.execute("SELECT * FROM jobs ORDER BY s_number")
        while True:
            rows = await cursor.fetchmany(500)
            if not rows:
                break
            output = io.StringIO()
            writer = csv.writer(output)
            for job in rows:
                writer.writerow([
                    job['id'], job['s_number'], job['title'],
                    job['priority_level'], job['created_at'], job['notes']
                ])
            yield output.getvalue()

    return StreamingResponse(
        generate(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=jobs.csv"}
    )

@app.get("/api/export/cam-items/csv")
async def export_cam_items_csv(
    db: aiosqlite.Connection = Depends(get_db),
    user: dict = Depends(get_current_user)
):
    """Export cam items to CSV (requires authentication)"""
    async def generate():
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow([
            's_number', 'set_no', 'cam_no', 'status_station', 'status_updated_at',
            'enter_die_steel', 'exit_die_steel', 'notes', 'eol_cycles_expected'
        ])
        yield output.getvalue()

        cursor = await db.execute(
            """
            SELECT c.*, j.s_number
            FROM cam_items c
            JOIN jobs j ON c.job_id = j.id
            ORDER BY j.s_number, c.set_no, c.cam_no
            """
        )
        while True:
            rows = await cursor.fetchmany(500)
            if not rows:
                break
            output = io.StringIO()
            writer = csv.writer(output)
            for item in rows:
                writer.writerow([
                    item['s_number'], item['set_no'], item['cam_no'],
                    item['status_station'], item['status_updated_at'],
                    item['enter_die_steel'], item['exit_die_steel'],
                    item['notes'], item['eol_cycles_expected']
                ])
            yield output.getvalue()

    return StreamingResponse(
        generate(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=cam_items.csv"}
    )

@app.get("/api/export/moves/csv")
async def export_moves_csv(
    db: aiosqlite.Connection = Depends(get_db),
    user: dict = Depends(get_current_user)
):
    """Export moves to CSV (requires authentication)"""
    async def generate():
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow([
            's_number', 'set_no', 'cam_no', 'from_station', 'to_station',
            'moved_at', 'operator', 'notes', 'undone'
        ])
        yield output.getvalue()

        cursor = await db.execute(
            """
            SELECT m.*, j.s_number, c.set_no, c.cam_no
            FROM moves m
            JOIN cam_items c ON m.cam_item_id = c.id
            JOIN jobs j ON c.job_id = j.id
            ORDER BY m.moved_at DESC
            """
        )
        while True:
            rows = await cursor.fetchmany(500)
            if not rows:
                break
            output = io.StringIO()
            writer = csv.writer(output)
            for move in rows:
                writer.writerow([
                    move['s_number'], move['set_no'], move['cam_no'],
                    move['from_station'], move['to_station'],
                    move['moved_at'], move['operator'], move['notes'], move['undone']
                ])
            yield output.getvalue()

    return StreamingResponse(
        generate(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=moves.csv"}
    )

@app.get("/api/export/database")
async def export_database(admin_user: dict = Depends(get_admin_user)):
    """Download the entire SQLite database file (requires admin authentication)"""
    return FileResponse(
        config.DATABASE_PATH,
        media_type="application/octet-stream",
        filename="cam_tracking_backup.db"
    )

@app.post("/api/import/database")
async def import_database(
    file: UploadFile = File(...),
    admin_user: dict = Depends(get_admin_user)
):
    """
    Import/restore a SQLite database file (requires admin authentication).
    WARNING: This replaces ALL current data!
    """
    import os
    import tempfile
    import sqlite3
    from datetime import datetime

    # Validate file extension
    if not file.filename.endswith(('.db', '.sqlite', '.sqlite3')):
        raise HTTPException(
            status_code=400,
            detail="Invalid file type. Must be a SQLite database file (.db, .sqlite, or .sqlite3)"
        )

    # Define max file size (100MB)
    MAX_FILE_SIZE = 100 * 1024 * 1024

    # Read and validate file size
    content = await file.read()
    file_size = len(content)

    if file_size > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=413,
            detail=f"File too large. Maximum size is 100MB, uploaded file is {file_size / (1024*1024):.2f}MB"
        )

    if file_size == 0:
        raise HTTPException(
            status_code=400,
            detail="Uploaded file is empty"
        )

    logger.info(f"Admin '{admin_user['username']}' uploading database file: {file.filename} ({file_size / (1024*1024):.2f}MB)")

    # Create a temporary file to validate the uploaded database
    with tempfile.NamedTemporaryFile(delete=False, suffix='.db') as temp_file:
        temp_path = temp_file.name

        try:
            # Write uploaded file to temp location
            temp_file.write(content)
            temp_file.flush()

            # Validate it's a valid SQLite database and has expected tables
            try:
                conn = sqlite3.connect(temp_path)
                cursor = conn.cursor()

                # Check for required tables
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
                tables = {row[0] for row in cursor.fetchall()}
                required_tables = {'jobs', 'cam_items', 'moves'}

                if not required_tables.issubset(tables):
                    missing = required_tables - tables
                    raise HTTPException(
                        status_code=400,
                        detail=f"Invalid database: missing required tables: {', '.join(missing)}"
                    )

                # Get counts for response
                cursor.execute("SELECT COUNT(*) FROM jobs")
                jobs_count = cursor.fetchone()[0]

                cursor.execute("SELECT COUNT(*) FROM cam_items")
                cam_items_count = cursor.fetchone()[0]

                cursor.execute("SELECT COUNT(*) FROM moves")
                moves_count = cursor.fetchone()[0]

                conn.close()

            except sqlite3.DatabaseError as e:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid SQLite database file: {str(e)}"
                )

            # Create backup of current database
            backup_path = f"{config.DATABASE_PATH}.backup.{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            if os.path.exists(config.DATABASE_PATH):
                shutil.copy2(config.DATABASE_PATH, backup_path)

            # Replace current database with uploaded one
            # Note: existing aiosqlite connections from concurrent requests may fail
            # after this operation. The frontend triggers a page reload to recover.
            logger.warning(f"Admin '{admin_user['username']}' replacing database file — active connections will be invalidated")
            shutil.move(temp_path, config.DATABASE_PATH)

            # Invalidate all sessions since user data may have changed
            active_sessions.clear()

            logger.info(f"Database imported successfully by admin '{admin_user['username']}'. Jobs: {jobs_count}, CAMs: {cam_items_count}, Moves: {moves_count}")

            return {
                "message": "Database imported successfully",
                "backup_created": backup_path if os.path.exists(backup_path) else None,
                "jobs_count": jobs_count,
                "cam_items_count": cam_items_count,
                "moves_count": moves_count
            }

        except HTTPException:
            # Clean up temp file and re-raise
            if os.path.exists(temp_path):
                os.unlink(temp_path)
            raise
        except Exception as e:
            # Clean up temp file
            if os.path.exists(temp_path):
                os.unlink(temp_path)
            logger.error(f"Database import failed for admin '{admin_user['username']}': {str(e)}")
            raise HTTPException(status_code=500, detail=f"Import failed: {str(e)}")

if __name__ == "__main__":
    import uvicorn

    # Initialize database if it doesn't exist
    init_database()
    migrate_database()

    # Run server
    uvicorn.run(
        "main:app",
        host=config.HOST,
        port=config.PORT,
        reload=False
    )
