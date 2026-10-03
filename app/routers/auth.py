"""Login/logout and user management."""
import logging

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.deps import (bearer_token, cleanup_sessions, create_session, delete_session,
                      get_admin_user, get_current_user, invalidate_user_sessions)
from app.limiter import limiter
from app.schemas import LoginRequest, UserCreate, UserUpdate
from app.utils import USER_COLUMNS, fetch_all, fetch_one, require_one, update_row
from database import get_db, hash_pin, verify_pin

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api")


async def _pin_in_use(db, pin: str, exclude_user_id: int = None) -> bool:
    """PINs identify users, so they must be unique among active accounts."""
    rows = await fetch_all(db, "SELECT id, pin_hash FROM users WHERE active = 1")
    return any(verify_pin(pin, u["pin_hash"]) for u in rows if u["id"] != exclude_user_id)


# ========== Auth ==========

@router.post("/auth/login")
@limiter.limit("20/minute")
async def login(request: Request, login_req: LoginRequest, db: aiosqlite.Connection = Depends(get_db)):
    """Log in by PIN only. The system matches the PIN to a user."""
    await cleanup_sessions(db)

    users = await fetch_all(db, "SELECT id, username, display_name, pin_hash, role FROM users WHERE active = 1")
    for user in users:
        if verify_pin(login_req.pin, user["pin_hash"]):
            token = await create_session(db, user)
            logger.info(f"User '{user['username']}' logged in successfully")
            return {
                "token": token,
                "user": {k: user[k] for k in ("id", "username", "display_name", "role")}
            }

    logger.warning("Failed login attempt with invalid PIN")
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid PIN")


@router.post("/auth/logout")
async def logout(request: Request, db: aiosqlite.Connection = Depends(get_db)):
    """Log out and invalidate the session."""
    token = bearer_token(request)
    if token:
        session = await fetch_one(db, "SELECT username FROM sessions WHERE token = ?", (token,))
        if session:
            logger.info(f"User '{session['username']}' logged out")
            await delete_session(db, token)
    return {"success": True}


@router.get("/auth/me")
async def get_me(user: dict = Depends(get_current_user)):
    return {k: user[k] for k in ("user_id", "username", "display_name", "role")}


# ========== Users (admin) ==========

@router.get("/users")
async def list_users(admin: dict = Depends(get_admin_user), db: aiosqlite.Connection = Depends(get_db)):
    """List all users. Never returns PIN hashes."""
    return await fetch_all(db, f"SELECT {USER_COLUMNS} FROM users ORDER BY username")


@router.post("/users")
async def create_user(user_data: UserCreate, admin: dict = Depends(get_admin_user),
                      db: aiosqlite.Connection = Depends(get_db)):
    if await _pin_in_use(db, user_data.pin):
        raise HTTPException(status_code=400, detail="This PIN is already in use by another user")

    try:
        cursor = await db.execute(
            "INSERT INTO users (username, display_name, pin_hash, role) VALUES (?, ?, ?, ?)",
            (user_data.username, user_data.display_name, hash_pin(user_data.pin), user_data.role)
        )
        await db.commit()
    except aiosqlite.IntegrityError:
        raise HTTPException(status_code=400, detail=f"Username '{user_data.username}' already exists")

    logger.info(f"Admin '{admin['username']}' created user '{user_data.username}' (role: {user_data.role})")
    return {
        "id": cursor.lastrowid,
        "username": user_data.username,
        "display_name": user_data.display_name,
        "role": user_data.role,
        "active": True
    }


@router.patch("/users/{user_id}")
async def update_user(user_id: int, user_data: UserUpdate, admin: dict = Depends(get_admin_user),
                      db: aiosqlite.Connection = Depends(get_db)):
    await require_one(db, "SELECT id FROM users WHERE id = ?", (user_id,), "User not found")

    fields = user_data.model_dump(exclude_none=True, exclude={"pin", "active"})
    if user_data.active is not None:
        fields["active"] = 1 if user_data.active else 0
    if user_data.pin is not None:
        if await _pin_in_use(db, user_data.pin, exclude_user_id=user_id):
            raise HTTPException(status_code=400, detail="This PIN is already in use by another user")
        fields["pin_hash"] = hash_pin(user_data.pin)

    await update_row(db, "users", user_id, fields, touch="updated_at")
    await db.commit()

    if user_data.active is False:
        await invalidate_user_sessions(db, user_id)

    logger.info(f"Admin '{admin['username']}' updated user id={user_id}")
    return await fetch_one(db, f"SELECT {USER_COLUMNS} FROM users WHERE id = ?", (user_id,))


@router.delete("/users/{user_id}")
async def delete_user(user_id: int, admin: dict = Depends(get_admin_user),
                      db: aiosqlite.Connection = Depends(get_db)):
    """Deactivate a user (soft delete) and end their sessions."""
    if admin["user_id"] == user_id:
        raise HTTPException(status_code=400, detail="Cannot deactivate your own account")

    user = await require_one(db, "SELECT username FROM users WHERE id = ?", (user_id,), "User not found")
    await db.execute("UPDATE users SET active = 0, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (user_id,))
    await db.commit()
    await invalidate_user_sessions(db, user_id)

    logger.info(f"Admin '{admin['username']}' deactivated user '{user['username']}'")
    return {"success": True, "message": f"User '{user['username']}' deactivated"}
