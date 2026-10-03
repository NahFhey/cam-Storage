"""Session storage and authentication dependencies."""
import secrets
from datetime import datetime, timedelta
from typing import Optional

import aiosqlite
from fastapi import Depends, HTTPException, Request, status

import config
from database import get_db


def bearer_token(request: Request) -> Optional[str]:
    auth_header = request.headers.get("Authorization", "")
    return auth_header[7:] if auth_header.startswith("Bearer ") else None


async def create_session(db, user: dict) -> str:
    """Create a new session for a user and return the token."""
    token = secrets.token_urlsafe(32)
    expires_at = (datetime.now() + timedelta(hours=config.SESSION_DURATION_HOURS)).isoformat()
    await db.execute(
        """INSERT INTO sessions (token, user_id, username, display_name, role, expires_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (token, user["id"], user["username"], user["display_name"], user["role"], expires_at)
    )
    await db.commit()
    return token


async def get_session(db, token: str) -> Optional[dict]:
    """Get a valid session by token, or None if expired/missing."""
    cursor = await db.execute(
        "SELECT user_id, username, display_name, role, expires_at FROM sessions WHERE token = ?",
        (token,)
    )
    row = await cursor.fetchone()
    if not row:
        return None
    session = dict(row)
    if datetime.fromisoformat(session["expires_at"]) <= datetime.now():
        await delete_session(db, token)
        return None
    return session


async def delete_session(db, token: str):
    await db.execute("DELETE FROM sessions WHERE token = ?", (token,))
    await db.commit()


async def cleanup_sessions(db):
    """Remove expired sessions."""
    await db.execute("DELETE FROM sessions WHERE expires_at <= ?", (datetime.now().isoformat(),))
    await db.commit()


async def invalidate_user_sessions(db, user_id: int):
    """Invalidate all sessions for a specific user."""
    await db.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
    await db.commit()


async def get_current_user(request: Request, db: aiosqlite.Connection = Depends(get_db)) -> dict:
    """Resolve the authenticated user from the Bearer session token."""
    token = bearer_token(request)
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    session = await get_session(db, token)
    if not session:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired or invalid")
    return session


async def get_admin_user(user: dict = Depends(get_current_user)) -> dict:
    """Require admin role."""
    if user["role"] != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    return user
