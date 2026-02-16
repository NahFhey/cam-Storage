# Testing Guide

## Running Tests

This project uses pytest for testing. Tests are located in the `tests/` directory.

### Install Dependencies

```bash
pip install -r requirements.txt
```

### Run All Tests

```bash
pytest tests/ -v
```

### Run Specific Test Files

```bash
# Entry parser tests (parsing manual CAM entries)
pytest tests/test_entry_parser.py -v

# Database and business logic tests
pytest tests/test_database.py -v

# API endpoint tests
pytest tests/test_api.py -v
```

### Run Tests with Coverage

```bash
pytest tests/ --cov=. --cov-report=html
```

## Test Structure

### test_entry_parser.py
- **TestParseManualEntry**: Tests for parsing various CAM entry formats
  - Full format: `S1793 SET1 CAM2`
  - Compact format: `1793-1-2`
  - Partial formats: `S1793 SET1`, `S1793 CAM2`, `S1793`
  - Case insensitivity and whitespace handling

- **TestResolveEntry**: Tests for resolving entries to database records
  - Exact matches
  - Multiple candidates
  - Job not found scenarios
  - CAM item not found scenarios

### test_database.py
- **TestDatabaseSchema**: Database schema and constraint tests
  - Schema initialization
  - Foreign key enforcement
  - Cascade deletes
  - Unique constraints
  - Config table operations

- **TestBusinessLogic**: Core business logic tests
  - CAM movement operations
  - Audit trail creation
  - Undo functionality
  - Hot list generation

### test_api.py
- **TestAuthentication**: PIN-based authentication tests
  - Unauthorized access rejection (no token)
  - Invalid PIN rejection
  - Valid PIN login and Bearer token issuance
  - Session-based access control

- **TestJobsAPI**: Job management endpoint tests
  - Listing jobs (with pagination)
  - Creating jobs (admin only)
  - Updating jobs
  - Deleting jobs
  - Duplicate prevention
  - Validation

- **TestMovesAPI**: CAM movement operation tests
  - Moving CAMs between stations
  - Same station move prevention
  - Undo operations
  - Material removal tracking (sharpen to cabinet)

- **TestSearchAPI**: Search functionality tests
- **TestConfigAPI**: Configuration management tests
- **TestValidation**: Input validation tests

## Test Status

**82 tests passing** across all test files (Entry Parser + Database/Business Logic + API)

## Test Fixtures

Available fixtures (see `tests/conftest.py`):
- `test_db_path`: Temporary SQLite database file, initialized with full schema
- `test_db`: Async `aiosqlite` connection to the temporary database
- `client`: FastAPI `TestClient` with isolated test database and rate limiting disabled
- `auth_headers`: Admin Bearer token headers (logs in with default admin PIN `1234`)
- `user_auth_headers`: Regular user Bearer token headers (creates a test worker, logs in with PIN `5678`)

## Writing New Tests

### Example Test

```python
import pytest

@pytest.mark.asyncio
async def test_my_feature(test_db):
    # Create test data
    cursor = await test_db.execute(
        "INSERT INTO jobs (s_number, title, priority_level) VALUES (?, ?, ?)",
        ("1234", "Test Job", "medium")
    )
    job_id = cursor.lastrowid
    await test_db.commit()

    # Test your feature
    # ...

    # Assert results
    assert job_id > 0
```

### Example API Test

```python
def test_create_job(client, auth_headers):
    response = client.post("/api/jobs", json={
        "s_number": "S9999",
        "title": "Test Job",
        "priority_level": "medium"
    }, headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["s_number"] == "9999"
```

## Continuous Integration

To run tests in CI/CD:

```yaml
# .github/workflows/test.yml
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
        run: pip install -r requirements.txt
      - name: Run tests
        run: pytest tests/ -v
```

## Test Coverage Goals

- **Entry Parser**: 100% (achieved)
- **Database/Business Logic**: 90%+ (achieved)
- **API Endpoints**: 80%+ (achieved)
- **Overall**: 85%+
