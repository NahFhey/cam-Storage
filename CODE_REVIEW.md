# Code Review: CAM Tracking Kiosk

## Overview
This is a comprehensive code review of the CAM Tracking Kiosk application. The application is well-structured and functional, but there are several areas for improvement across security, performance, code quality, and maintainability.

## Executive Summary

**Strengths:**
- Clean architecture with separated concerns (database, business logic, API)
- Good offline-first design for shop floor use
- Comprehensive feature set with analytics and export capabilities
- Touch-optimized UI design
- Flexible input parsing system

**Critical Issues:**
- 🔴 **SQL Injection vulnerability** in dynamic query building
- 🔴 **No authentication/authorization** - Admin functions are publicly accessible
- 🟡 **No input validation** on several endpoints
- 🟡 **Performance issues** with N+1 queries
- 🟡 **No automated tests**

---

## 1. Security Issues 🔴

### 1.1 SQL Injection Vulnerability (CRITICAL)
**Location:** `main.py:170`, `main.py:324`

**Issue:**
```python
# Line 170-171
await db.execute(
    f"UPDATE jobs SET {', '.join(updates)} WHERE id = ?",
    values
)
```

Using f-strings to build SQL queries is dangerous even though the field names come from controlled Pydantic models. If the model validation is bypassed or changed, this becomes an SQL injection vector.

**Fix:**
```python
# Build explicit UPDATE statements
update_parts = []
if job.title is not None:
    update_parts.append("title = ?")
    values.append(job.title)
# ... etc

if not update_parts:
    raise HTTPException(status_code=400, detail="No fields to update")

query = f"UPDATE jobs SET {', '.join(update_parts)} WHERE id = ?"
```

Or use a safer approach with explicit column mapping.

---

### 1.2 No Authentication/Authorization
**Location:** All API endpoints

**Issue:**
The application has no authentication mechanism. Anyone with network access can:
- Delete jobs and data
- Import malicious databases
- Modify configuration
- Access sensitive manufacturing data

**Recommendations:**
1. Implement basic authentication for admin endpoints
2. Use API keys for kiosk vs admin access
3. Add role-based access control (RBAC)
4. Consider JWT tokens for session management

**Example Implementation:**
```python
from fastapi import Security
from fastapi.security import HTTPBasic, HTTPBasicCredentials

security = HTTPBasic()

def get_current_user(credentials: HTTPBasicCredentials = Security(security)):
    # Implement your authentication logic
    if not verify_credentials(credentials):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    return credentials.username

# Protected endpoint
@app.delete("/api/jobs/{job_id}")
async def delete_job(job_id: int, user: str = Depends(get_current_user)):
    # ... existing code
```

---

### 1.3 Insecure File Upload
**Location:** `main.py:820-908` (import_database)

**Issues:**
1. No file size limit - could cause DoS
2. Temporary file uses predictable name
3. No virus scanning
4. Synchronous file operations could block event loop

**Fix:**
```python
@app.post("/api/import/database")
async def import_database(
    file: UploadFile = File(..., max_size=100 * 1024 * 1024)  # 100MB limit
):
    # Validate file size
    content = await file.read()
    if len(content) > 100 * 1024 * 1024:  # 100MB
        raise HTTPException(status_code=413, detail="File too large")

    # Use secure temp file
    with tempfile.NamedTemporaryFile(delete=False, suffix='.db',
                                     dir='/secure/temp/dir') as temp_file:
        # ... rest of code
```

---

### 1.4 No Rate Limiting
**Location:** All endpoints

**Issue:**
No protection against brute force or DoS attacks.

**Fix:**
```python
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

@app.post("/api/moves")
@limiter.limit("100/minute")
async def move_cam(request: Request, move: MoveRequest, ...):
    # ... existing code
```

---

### 1.5 CSRF Protection
**Location:** Frontend forms

**Issue:**
No CSRF tokens on state-changing operations.

**Fix:**
Implement CSRF protection using `fastapi-csrf-protect` or similar.

---

## 2. Code Quality & Best Practices 🟡

### 2.1 Missing Type Hints
**Location:** Multiple files

**Issue:**
Inconsistent type hints reduce code clarity and prevent static analysis.

**Examples:**
```python
# entry_parser.py:107 - missing return type details
async def resolve_entry(db, entry: str) -> Dict:  # Too generic

# Should be:
from typing import TypedDict

class ResolveResult(TypedDict, total=False):
    status: str
    cam_item: Optional[Dict]
    candidates: Optional[List[Dict]]
    job: Optional[Dict]
    parsed: ParsedEntry

async def resolve_entry(db: aiosqlite.Connection, entry: str) -> ResolveResult:
```

---

### 2.2 Magic Numbers
**Location:** `business_logic.py:236-251`

**Issue:**
```python
if all_in_sharpen:
    priority_score += 100  # Magic number

if no_cabinet_with_active_set:
    priority_score += 2    # Magic number
```

**Fix:**
```python
# At top of file or in config
PRIORITY_BOOST_ALL_SHARPEN = 100
PRIORITY_BOOST_NO_CABINET = 2
PRIORITY_BOOST_NO_SETS = 2
PRIORITY_BOOST_ONE_SET = 1
PRIORITY_BOOST_REFILL = 1

# In function
if all_in_sharpen:
    priority_score += PRIORITY_BOOST_ALL_SHARPEN
```

---

### 2.3 Inconsistent Error Handling
**Location:** `main.py:570-571`

**Issue:**
```python
try:
    result = await resolve_entry(db, q)
    # ...
except:  # Bare except, no logging
    pass
```

**Fix:**
```python
except Exception as e:
    logger.warning(f"Entry resolution failed during search: {e}")
    # Don't fail the whole search if entry parsing fails
```

---

### 2.4 No Logging
**Location:** Throughout application

**Issue:**
Using `print()` statements instead of proper logging framework.

**Fix:**
```python
import logging

# In main.py
logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('cam_tracking.log'),
        logging.StreamHandler()
    ]
)

# Replace print statements
logger.info(f"Database initialized at {db_path}")
logger.error(f"Failed to move CAM {cam_item_id}: {e}")
```

---

### 2.5 Long Functions
**Location:** `main.py:820-908` (88 lines)

**Issue:**
`import_database` function is too long and does too many things.

**Fix:**
Break into smaller functions:
```python
async def validate_database_file(file: UploadFile) -> bytes:
    """Validate uploaded file and return contents"""
    # Validation logic

async def verify_database_schema(db_path: str) -> Dict[str, int]:
    """Verify database has required tables and return counts"""
    # Schema verification logic

async def backup_current_database() -> str:
    """Create backup of current database"""
    # Backup logic

@app.post("/api/import/database")
async def import_database(file: UploadFile = File(...)):
    content = await validate_database_file(file)
    counts = await verify_database_schema(temp_path)
    backup_path = await backup_current_database()
    # ... simplified main logic
```

---

### 2.6 Duplicate Code
**Location:** Multiple database query patterns

**Issue:**
Similar patterns repeated for fetching and converting rows to dicts.

**Fix:**
Create helper functions:
```python
async def fetch_one_as_dict(db, query: str, params: tuple) -> Optional[Dict]:
    """Execute query and return single row as dict"""
    cursor = await db.execute(query, params)
    row = await cursor.fetchone()
    return dict(row) if row else None

async def fetch_all_as_dict(db, query: str, params: tuple = ()) -> List[Dict]:
    """Execute query and return all rows as list of dicts"""
    cursor = await db.execute(query, params)
    rows = await cursor.fetchall()
    return [dict(row) for row in rows]
```

---

## 3. Performance Issues 🟡

### 3.1 N+1 Query Problem
**Location:** `business_logic.py:315-326`

**Issue:**
Hot list generation makes an extra query for each job to check active sets.

**Fix:**
Use a single query with window functions or GROUP BY:
```python
cursor = await db.execute("""
    WITH active_sets AS (
        SELECT
            job_id,
            set_no,
            COUNT(*) as set_size,
            SUM(CASE WHEN status_station = 'active' THEN 1 ELSE 0 END) as active_in_set
        FROM cam_items
        GROUP BY job_id, set_no
        HAVING set_size = active_in_set AND active_in_set > 0
    )
    SELECT
        j.id,
        j.s_number,
        -- ... other fields
        COUNT(DISTINCT CASE WHEN c.status_station != 'refill' THEN c.set_no END) as available_sets,
        -- ... counts
        EXISTS(SELECT 1 FROM active_sets a WHERE a.job_id = j.id) as has_complete_active_set
    FROM jobs j
    LEFT JOIN cam_items c ON j.id = c.job_id
    GROUP BY j.id
""")
```

---

### 3.2 No Pagination
**Location:** `main.py:83-90` (list_jobs), `main.py:188-210` (list_cam_items)

**Issue:**
Could return thousands of records, causing memory and network issues.

**Fix:**
```python
@app.get("/api/jobs")
async def list_jobs(
    skip: int = 0,
    limit: int = 100,
    db: aiosqlite.Connection = Depends(get_db)
):
    """List jobs with pagination"""
    cursor = await db.execute(
        "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ? OFFSET ?",
        (limit, skip)
    )
    jobs = await cursor.fetchall()

    # Get total count
    cursor = await db.execute("SELECT COUNT(*) as count FROM jobs")
    total = (await cursor.fetchone())['count']

    return {
        "items": [dict(job) for job in jobs],
        "total": total,
        "skip": skip,
        "limit": limit
    }
```

---

### 3.3 No Database Connection Pooling
**Location:** `database.py:90-94`

**Issue:**
Creates new connection for every request.

**Fix:**
```python
# Use connection pool
from aiosqlite import connect
from contextlib import asynccontextmanager

class DatabasePool:
    def __init__(self, db_path: str, pool_size: int = 10):
        self.db_path = db_path
        self.pool_size = pool_size
        self._connections = []

    @asynccontextmanager
    async def acquire(self):
        # Implement connection pooling logic
        pass

# In main.py
@app.on_event("startup")
async def startup():
    app.state.db_pool = DatabasePool(config.DATABASE_PATH)

@app.on_event("shutdown")
async def shutdown():
    await app.state.db_pool.close_all()
```

---

### 3.4 Config Caching
**Location:** `business_logic.py:60-61`

**Issue:**
Fetches auto_bump config from database on every move operation.

**Fix:**
```python
# In-memory cache with TTL
from functools import lru_cache
from time import time

_config_cache = {}
_cache_ttl = 60  # seconds

async def get_cached_config_value(key: str, default: str = None) -> str:
    """Get config value with in-memory cache"""
    cache_key = f"config_{key}"
    now = time()

    if cache_key in _config_cache:
        value, timestamp = _config_cache[cache_key]
        if now - timestamp < _cache_ttl:
            return value

    value = await get_config_value(key, default)
    _config_cache[cache_key] = (value, now)
    return value
```

---

### 3.5 Missing Database Indexes
**Location:** `database.py`

**Issue:**
Could benefit from composite indexes for common queries.

**Fix:**
```sql
-- Add to SCHEMA_SQL
CREATE INDEX IF NOT EXISTS idx_cam_items_job_set_cam ON cam_items(job_id, set_no, cam_no);
CREATE INDEX IF NOT EXISTS idx_cam_items_job_station ON cam_items(job_id, status_station);
CREATE INDEX IF NOT EXISTS idx_moves_cam_undone ON moves(cam_item_id, undone);
CREATE INDEX IF NOT EXISTS idx_moves_station_undone ON moves(to_station, undone);
```

---

## 4. Database & Data Integrity 🟡

### 4.1 Foreign Key Enforcement
**Location:** `database.py`

**Issue:**
SQLite doesn't enforce foreign keys by default.

**Fix:**
```python
async def get_db():
    """Get async database connection with foreign keys enabled"""
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA foreign_keys = ON")  # Enable FK enforcement
        yield db
```

---

### 4.2 No Database Migrations Framework
**Location:** `database.py:119-174`

**Issue:**
Ad-hoc migration logic that's hard to track and version.

**Fix:**
Use Alembic or similar:
```bash
pip install alembic
alembic init migrations
```

---

### 4.3 Missing Constraints
**Location:** `database.py`

**Issue:**
Could validate more at database level.

**Fix:**
```sql
-- Add CHECK constraints
ALTER TABLE cam_items ADD CONSTRAINT check_set_no_positive
    CHECK (set_no > 0);
ALTER TABLE cam_items ADD CONSTRAINT check_cam_no_positive
    CHECK (cam_no > 0);
ALTER TABLE moves ADD CONSTRAINT check_material_removed_range
    CHECK (material_removed IS NULL OR (material_removed >= 0 AND material_removed <= 1.0));
```

---

### 4.4 Data Validation
**Location:** `main.py:421-432`

**Issue:**
Material removed validation only happens at move time, not at model level.

**Fix:**
```python
from pydantic import validator

class MoveRequest(BaseModel):
    cam_item_id: int
    to_station: str
    operator: Optional[str] = None
    notes: Optional[str] = None
    auto_bump: Optional[bool] = None
    material_removed: Optional[float] = None

    @validator('material_removed')
    def validate_material_removed(cls, v):
        if v is not None and (v < 0 or v > 1.0):
            raise ValueError('Material removed must be between 0.000 and 1.000 inches')
        return v

    @validator('to_station')
    def validate_station(cls, v):
        if v not in ['active', 'sharpen', 'cabinet', 'refill']:
            raise ValueError(f'Invalid station: {v}')
        return v
```

---

## 5. Frontend Issues 🟡

### 5.1 No Request Timeout
**Location:** `static/js/api.js:6-31`

**Issue:**
API calls have no timeout, could hang indefinitely.

**Fix:**
```javascript
async function apiCall(endpoint, options = {}) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 30000); // 30s timeout

    try {
        const response = await fetch(`${API_BASE}${endpoint}`, {
            ...options,
            signal: controller.signal,
            headers: {
                'Content-Type': 'application/json',
                ...options.headers
            }
        });
        clearTimeout(timeout);

        // ... rest of code
    } catch (error) {
        clearTimeout(timeout);
        if (error.name === 'AbortError') {
            throw new Error('Request timeout - please try again');
        }
        throw error;
    }
}
```

---

### 5.2 No Loading States
**Location:** Frontend JavaScript

**Issue:**
No visual feedback during API calls.

**Fix:**
```javascript
let isLoading = false;

async function apiCall(endpoint, options = {}) {
    if (isLoading) {
        throw new Error('Request already in progress');
    }

    isLoading = true;
    showLoadingSpinner();

    try {
        const response = await fetch(/* ... */);
        return result;
    } finally {
        isLoading = false;
        hideLoadingSpinner();
    }
}
```

---

### 5.3 Unsafe Confirmation Dialog
**Location:** `static/js/api.js:196`

**Issue:**
Browser confirm() can be dismissed accidentally, risking data loss.

**Fix:**
Create a proper modal with clear warning and typed confirmation:
```javascript
async function confirmDangerousAction(message, confirmText = 'DELETE') {
    return new Promise((resolve) => {
        const modal = createConfirmModal(message, confirmText);
        modal.onConfirm = () => resolve(true);
        modal.onCancel = () => resolve(false);
        modal.show();
    });
}
```

---

## 6. Error Handling 🟡

### 6.1 Silent Failures
**Location:** Multiple places

**Issue:**
Errors caught but not logged or reported.

**Examples:**
```python
# main.py:371-373
except aiosqlite.IntegrityError:
    # Skip if already exists
    pass  # Should log this
```

**Fix:**
```python
except aiosqlite.IntegrityError as e:
    logger.warning(f"Skipping duplicate CAM item: {set_no}-{cam_no}: {e}")
    continue
```

---

### 6.2 Generic Error Messages
**Location:** Various endpoints

**Issue:**
Error messages don't help with debugging.

**Fix:**
```python
# Instead of:
raise ValueError(f"Invalid station: {to_station}")

# Use:
raise ValueError(
    f"Invalid station '{to_station}'. "
    f"Must be one of: {', '.join(config.STATIONS)}"
)
```

---

### 6.3 No Error Tracking
**Location:** Application-wide

**Issue:**
No integration with error tracking services.

**Recommendation:**
Integrate Sentry or similar:
```python
import sentry_sdk
from sentry_sdk.integrations.fastapi import FastApiIntegration

sentry_sdk.init(
    dsn="your-dsn",
    integrations=[FastApiIntegration()],
    traces_sample_rate=0.1,
    environment="production"
)
```

---

## 7. Testing 🔴

### 7.1 No Automated Tests
**Location:** Entire project

**Issue:**
No unit tests, integration tests, or E2E tests.

**Recommendations:**

**Unit Tests:**
```python
# tests/test_entry_parser.py
import pytest
from entry_parser import parse_manual_entry

def test_parse_full_format():
    result = parse_manual_entry("S1793 SET1 CAM2")
    assert result.s_number == "1793"
    assert result.set_no == 1
    assert result.cam_no == 2
    assert result.confidence == "exact"

def test_parse_compact_format():
    result = parse_manual_entry("1793-1-2")
    assert result.s_number == "1793"
    assert result.set_no == 1
    assert result.cam_no == 2
```

**Integration Tests:**
```python
# tests/test_api.py
import pytest
from fastapi.testclient import TestClient
from main import app

@pytest.fixture
def client():
    return TestClient(app)

def test_create_job(client):
    response = client.post("/api/jobs", json={
        "s_number": "S1234",
        "title": "Test Job",
        "priority_level": "medium"
    })
    assert response.status_code == 200
    assert response.json()["s_number"] == "S1234"
```

**E2E Tests:**
```python
# tests/test_e2e.py
from playwright.async_api import async_playwright

async def test_move_workflow():
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()
        await page.goto("http://localhost:8000")
        # Test complete workflow
```

---

### 7.2 No CI/CD
**Location:** Project root

**Issue:**
No automated testing or deployment pipeline.

**Fix:**
Create `.github/workflows/test.yml`:
```yaml
name: Tests

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v2
      - name: Set up Python
        uses: actions/setup-python@v2
        with:
          python-version: '3.10'
      - name: Install dependencies
        run: |
          pip install -r requirements.txt
          pip install pytest pytest-asyncio pytest-cov
      - name: Run tests
        run: pytest --cov=. tests/
      - name: Upload coverage
        uses: codecov/codecov-action@v2
```

---

## 8. Documentation 🟡

### 8.1 No API Documentation
**Location:** FastAPI app

**Issue:**
FastAPI auto-generates docs, but models lack descriptions.

**Fix:**
```python
class JobCreate(BaseModel):
    """Create a new production job"""
    s_number: str = Field(..., description="Job number (e.g., 'S1793')", regex=r"^S?\d+$")
    title: Optional[str] = Field(None, description="Job description or part name")
    priority_level: str = Field("low", description="Priority level", regex="^(low|medium|high|urgent|top)$")
    notes: Optional[str] = Field(None, description="Additional notes or comments")

    class Config:
        schema_extra = {
            "example": {
                "s_number": "S1793",
                "title": "Widget Punch Die",
                "priority_level": "high",
                "notes": "Rush order - customer waiting"
            }
        }
```

---

### 8.2 Missing Docstrings
**Location:** Various functions

**Issue:**
Not all functions have docstrings explaining parameters and return values.

**Fix:**
```python
async def move_cam_to_station(
    db: aiosqlite.Connection,
    cam_item_id: int,
    to_station: str,
    operator: Optional[str] = None,
    notes: Optional[str] = None,
    auto_bump: Optional[bool] = None,
    material_removed: Optional[float] = None
) -> Dict[str, Any]:
    """
    Move a CAM item to a new station with optional auto-bump.

    Args:
        db: Database connection
        cam_item_id: ID of the CAM item to move
        to_station: Destination station ('active', 'sharpen', 'cabinet', 'refill')
        operator: Name of operator performing the move (defaults to config.DEFAULT_OPERATOR)
        notes: Optional notes about the move
        auto_bump: Override auto-bump setting (defaults to config value)
        material_removed: Amount of material removed in inches (required for sharpen->cabinet)

    Returns:
        Dict containing:
            - success: bool
            - cam_item_id: int
            - move_id: int
            - from_station: str
            - to_station: str
            - auto_bumped: Optional[Dict] with details of auto-bumped item

    Raises:
        ValueError: If station is invalid, CAM not found, or already at destination

    Example:
        >>> result = await move_cam_to_station(db, 42, 'sharpen', operator='John')
        >>> print(result['success'])
        True
    """
```

---

## 9. Architecture Improvements 🟡

### 9.1 Separate Business Logic from API
**Location:** `main.py`

**Issue:**
Some business logic mixed with API handlers.

**Fix:**
Move more logic to `business_logic.py` or create service layer:
```python
# services/job_service.py
class JobService:
    def __init__(self, db: aiosqlite.Connection):
        self.db = db

    async def create_job(self, job_data: JobCreate) -> Dict:
        """Create a new job with validation"""
        # Business logic here

    async def get_job_with_stats(self, job_id: int) -> Dict:
        """Get job with computed statistics"""
        # Complex queries here

# In main.py
@app.post("/api/jobs")
async def create_job(job: JobCreate, db = Depends(get_db)):
    service = JobService(db)
    return await service.create_job(job)
```

---

### 9.2 Environment-Based Configuration
**Location:** `config.py`

**Issue:**
No differentiation between dev/staging/production.

**Fix:**
```python
import os
from enum import Enum

class Environment(str, Enum):
    DEVELOPMENT = "development"
    STAGING = "staging"
    PRODUCTION = "production"

ENV = os.getenv("APP_ENV", Environment.DEVELOPMENT)

# Environment-specific settings
if ENV == Environment.PRODUCTION:
    DEBUG = False
    LOG_LEVEL = "WARNING"
    ENABLE_DOCS = False
elif ENV == Environment.STAGING:
    DEBUG = True
    LOG_LEVEL = "INFO"
    ENABLE_DOCS = True
else:  # DEVELOPMENT
    DEBUG = True
    LOG_LEVEL = "DEBUG"
    ENABLE_DOCS = True
```

---

## 10. Deployment & Operations 🟡

### 10.1 Health Check Endpoint
**Location:** Missing

**Fix:**
```python
@app.get("/health")
async def health_check(db = Depends(get_db)):
    """Health check endpoint for monitoring"""
    try:
        # Test database connection
        await db.execute("SELECT 1")

        # Check disk space
        import shutil
        stats = shutil.disk_usage(os.path.dirname(config.DATABASE_PATH))
        free_gb = stats.free / (1024**3)

        return {
            "status": "healthy",
            "database": "connected",
            "free_disk_gb": round(free_gb, 2)
        }
    except Exception as e:
        return JSONResponse(
            status_code=503,
            content={"status": "unhealthy", "error": str(e)}
        )
```

---

### 10.2 Graceful Shutdown
**Location:** `main.py`

**Fix:**
```python
@app.on_event("shutdown")
async def shutdown_event():
    """Graceful shutdown - close connections and save state"""
    logger.info("Shutting down application...")
    # Close database connections
    # Flush caches
    # Save any pending operations
```

---

### 10.3 Database Backup Automation
**Location:** Missing

**Recommendation:**
```bash
#!/bin/bash
# scripts/backup_database.sh

BACKUP_DIR="/var/backups/cam-tracking"
DB_PATH="./cam_tracking.db"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)

mkdir -p "$BACKUP_DIR"
sqlite3 "$DB_PATH" ".backup '$BACKUP_DIR/cam_tracking_$TIMESTAMP.db'"

# Keep only last 30 days
find "$BACKUP_DIR" -name "cam_tracking_*.db" -mtime +30 -delete
```

Add to crontab:
```
0 2 * * * /path/to/backup_database.sh
```

---

## 11. Monitoring & Observability 🟡

### 11.1 Metrics Collection
**Location:** Missing

**Recommendation:**
Add Prometheus metrics:
```python
from prometheus_client import Counter, Histogram, generate_latest

# Metrics
move_counter = Counter('cam_moves_total', 'Total number of CAM moves', ['from_station', 'to_station'])
api_duration = Histogram('api_request_duration_seconds', 'API request duration', ['endpoint'])

@app.middleware("http")
async def metrics_middleware(request: Request, call_next):
    start_time = time.time()
    response = await call_next(request)
    duration = time.time() - start_time
    api_duration.labels(endpoint=request.url.path).observe(duration)
    return response

@app.get("/metrics")
async def metrics():
    return Response(generate_latest(), media_type="text/plain")
```

---

## Priority Recommendations

### Immediate (Fix ASAP):
1. 🔴 Fix SQL injection vulnerability in `main.py:170, 324`
2. 🔴 Add authentication to admin endpoints
3. 🔴 Add file size limits to database import
4. 🔴 Enable foreign key constraints
5. 🔴 Add logging framework

### Short Term (Next Sprint):
1. 🟡 Add input validation to all Pydantic models
2. 🟡 Implement pagination for list endpoints
3. 🟡 Fix N+1 queries in hot list generation
4. 🟡 Add request timeouts in frontend
5. 🟡 Create basic unit tests

### Medium Term (Next Month):
1. 🟡 Implement rate limiting
2. 🟡 Add CSRF protection
3. 🟡 Set up CI/CD pipeline
4. 🟡 Add metrics and monitoring
5. 🟡 Implement connection pooling

### Long Term (Roadmap):
1. Add comprehensive test suite (>80% coverage)
2. Implement proper migration framework
3. Add error tracking integration
4. Create admin authentication system
5. Add audit logging for all operations

---

## Testing Checklist

Before deploying fixes:
- [ ] All critical security issues resolved
- [ ] Unit tests written for business logic
- [ ] Integration tests for API endpoints
- [ ] Manual testing in kiosk environment
- [ ] Load testing with expected data volumes
- [ ] Security scan completed
- [ ] Code review by team member
- [ ] Documentation updated

---

## Conclusion

The CAM Tracking Kiosk is a well-designed application with good separation of concerns and comprehensive features. However, there are critical security issues that need immediate attention, particularly around SQL injection and authentication.

The recommended improvements will:
- **Improve security** by fixing vulnerabilities and adding authentication
- **Enhance performance** through query optimization and caching
- **Increase reliability** with better error handling and testing
- **Improve maintainability** through better code organization and documentation

Estimated effort to implement all recommendations: **4-6 weeks** for a single developer.

---

**Reviewed by:** Claude Code Agent
**Date:** 2026-01-19
**Version:** 1.0.0
