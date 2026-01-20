"""
Pytest configuration and shared fixtures
"""
import pytest
import asyncio
import tempfile
import os
from fastapi.testclient import TestClient
import aiosqlite

# Add parent directory to path for imports
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from database import init_database
from main import app


@pytest.fixture(scope="session")
def event_loop():
    """Create an event loop for the test session"""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def test_db_path():
    """Create a temporary database for testing"""
    with tempfile.NamedTemporaryFile(delete=False, suffix='.db') as f:
        db_path = f.name

    # Initialize test database
    init_database(db_path)

    yield db_path

    # Cleanup
    if os.path.exists(db_path):
        os.unlink(db_path)


@pytest.fixture
async def test_db(test_db_path):
    """Get async database connection for testing"""
    async with aiosqlite.connect(test_db_path) as db:
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA foreign_keys = ON")
        yield db


@pytest.fixture
def client():
    """FastAPI test client"""
    return TestClient(app)


@pytest.fixture
def auth_headers():
    """Authentication headers for admin endpoints"""
    import base64
    credentials = base64.b64encode(b"admin:admin123").decode("utf-8")
    return {"Authorization": f"Basic {credentials}"}
