"""
Pytest configuration and shared fixtures
"""
import pytest
import tempfile
import os
from fastapi.testclient import TestClient
import aiosqlite

# Add parent directory to path for imports
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from database import init_database, get_db
from main import app, limiter


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
def client(test_db_path):
    """FastAPI test client with isolated test database"""
    # Also patch config.DATABASE_PATH so that functions using
    # get_db_connection() directly (e.g. get_config_value) use the test DB
    original_db_path = config.DATABASE_PATH
    config.DATABASE_PATH = test_db_path

    # Disable rate limiting during tests to avoid 429 errors
    limiter.enabled = False

    async def override_get_db():
        async with aiosqlite.connect(test_db_path) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("PRAGMA foreign_keys = ON")
            yield db

    app.dependency_overrides[get_db] = override_get_db
    yield TestClient(app)
    app.dependency_overrides.clear()
    config.DATABASE_PATH = original_db_path
    limiter.enabled = True


@pytest.fixture
def auth_headers(client):
    """Authentication headers using PIN-based login.

    Logs in with the default admin PIN (1234) and returns
    Bearer token headers for authenticated requests.
    """
    response = client.post("/api/auth/login", json={"pin": "1234"})
    assert response.status_code == 200, f"Login failed: {response.text}"
    token = response.json()["token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def user_auth_headers(client, auth_headers):
    """Authentication headers for a non-admin user.

    Creates a regular user via admin, logs in as that user,
    and returns Bearer token headers.
    """
    # Create a regular user via admin endpoint
    client.post("/api/users", json={
        "username": "testworker",
        "display_name": "Test Worker",
        "pin": "5678",
        "role": "user"
    }, headers=auth_headers)

    # Log in as the regular user
    response = client.post("/api/auth/login", json={"pin": "5678"})
    assert response.status_code == 200, f"User login failed: {response.text}"
    token = response.json()["token"]
    return {"Authorization": f"Bearer {token}"}
