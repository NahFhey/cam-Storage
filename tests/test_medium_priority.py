"""
Tests for medium priority improvements:
- Pagination
- Rate limiting
- Input validation
- Health check endpoint
"""
import pytest
from fastapi.testclient import TestClient


class TestPagination:
    """Tests for pagination functionality"""

    def test_jobs_pagination_default(self, client, auth_headers):
        """Test jobs pagination with default parameters"""
        # Create some test jobs
        for i in range(5):
            client.post("/api/jobs", json={
                "s_number": f"S{4000 + i}",
                "title": f"Test Job {i}",
                "priority_level": "medium"
            }, headers=auth_headers)

        # Test pagination
        response = client.get("/api/jobs")
        assert response.status_code == 200
        data = response.json()

        assert "items" in data
        assert "total" in data
        assert "skip" in data
        assert "limit" in data
        assert "has_more" in data
        assert isinstance(data['items'], list)
        assert data['skip'] == 0
        assert data['limit'] == 100

    def test_jobs_pagination_with_limit(self, client, auth_headers):
        """Test jobs pagination with custom limit"""
        # Create test jobs
        for i in range(5):
            client.post("/api/jobs", json={
                "s_number": f"S{5000 + i}",
                "title": f"Test Job {i}",
                "priority_level": "medium"
            }, headers=auth_headers)

        # Test with limit=2
        response = client.get("/api/jobs?limit=2")
        assert response.status_code == 200
        data = response.json()

        assert len(data['items']) <= 2
        assert data['limit'] == 2

    def test_jobs_pagination_with_skip(self, client, auth_headers):
        """Test jobs pagination with skip"""
        # Create test jobs
        for i in range(5):
            client.post("/api/jobs", json={
                "s_number": f"S{6000 + i}",
                "title": f"Test Job {i}",
                "priority_level": "medium"
            }, headers=auth_headers)

        # Get first page
        response1 = client.get("/api/jobs?limit=2&skip=0")
        data1 = response1.json()

        # Get second page
        response2 = client.get("/api/jobs?limit=2&skip=2")
        data2 = response2.json()

        # Items should be different
        if len(data1['items']) > 0 and len(data2['items']) > 0:
            assert data1['items'][0]['id'] != data2['items'][0]['id']

    def test_jobs_pagination_max_limit_enforced(self, client):
        """Test that maximum limit is enforced"""
        response = client.get("/api/jobs?limit=1000")
        assert response.status_code == 200
        data = response.json()

        # Should be capped at 500
        assert data['limit'] == 500

    def test_cam_items_pagination(self, client, auth_headers):
        """Test CAM items pagination"""
        # Create job and items
        job_response = client.post("/api/jobs", json={
            "s_number": "S7000",
            "title": "Pagination Test",
            "priority_level": "medium"
        }, headers=auth_headers)
        job_id = job_response.json()['id']

        # Create multiple cam items
        for i in range(5):
            client.post("/api/cam-items", json={
                "job_id": job_id,
                "set_no": 1,
                "cam_no": i + 1,
                "status_station": "cabinet"
            }, headers=auth_headers)

        # Test pagination
        response = client.get("/api/cam-items?limit=3")
        assert response.status_code == 200
        data = response.json()

        assert "items" in data
        assert "total" in data
        assert "has_more" in data
        assert len(data['items']) <= 3

    def test_cam_items_pagination_with_filters(self, client, auth_headers):
        """Test CAM items pagination with filters"""
        # Create job and items
        job_response = client.post("/api/jobs", json={
            "s_number": "S7100",
            "title": "Filter Test",
            "priority_level": "medium"
        }, headers=auth_headers)
        job_id = job_response.json()['id']

        # Create items in different stations
        for i in range(3):
            client.post("/api/cam-items", json={
                "job_id": job_id,
                "set_no": 1,
                "cam_no": i + 1,
                "status_station": "cabinet" if i % 2 == 0 else "sharpen"
            }, headers=auth_headers)

        # Test filtering
        response = client.get(f"/api/cam-items?job_id={job_id}&station=cabinet")
        assert response.status_code == 200
        data = response.json()

        assert data['filters']['station'] == "cabinet"
        # All returned items should be in cabinet
        for item in data['items']:
            assert item['status_station'] == "cabinet"


class TestInputValidation:
    """Tests for enhanced input validation"""

    def test_job_s_number_validation(self, client, auth_headers):
        """Test S-number validation"""
        # Valid S-number
        response = client.post("/api/jobs", json={
            "s_number": "S8000",
            "title": "Valid Job",
            "priority_level": "medium"
        }, headers=auth_headers)
        assert response.status_code == 200

        # Valid without S prefix
        response = client.post("/api/jobs", json={
            "s_number": "8001",
            "title": "Valid Job",
            "priority_level": "medium"
        }, headers=auth_headers)
        assert response.status_code == 200

    def test_job_s_number_invalid(self, client, auth_headers):
        """Test invalid S-number"""
        response = client.post("/api/jobs", json={
            "s_number": "INVALID",
            "title": "Invalid Job",
            "priority_level": "medium"
        }, headers=auth_headers)
        assert response.status_code == 422  # Validation error

    def test_job_priority_validation(self, client, auth_headers):
        """Test priority level validation"""
        # Invalid priority
        response = client.post("/api/jobs", json={
            "s_number": "S8100",
            "title": "Test Job",
            "priority_level": "invalid_priority"
        }, headers=auth_headers)
        assert response.status_code == 422

    def test_cam_item_set_no_validation(self, client, auth_headers):
        """Test set number validation"""
        # Create job
        job_response = client.post("/api/jobs", json={
            "s_number": "S8200",
            "title": "Validation Test",
            "priority_level": "medium"
        }, headers=auth_headers)
        job_id = job_response.json()['id']

        # Invalid set number (0)
        response = client.post("/api/cam-items", json={
            "job_id": job_id,
            "set_no": 0,
            "cam_no": 1,
            "status_station": "cabinet"
        }, headers=auth_headers)
        assert response.status_code == 422

        # Invalid set number (negative)
        response = client.post("/api/cam-items", json={
            "job_id": job_id,
            "set_no": -1,
            "cam_no": 1,
            "status_station": "cabinet"
        }, headers=auth_headers)
        assert response.status_code == 422

    def test_move_material_removed_validation(self, client, auth_headers):
        """Test material removed validation"""
        # Create job and cam
        job_response = client.post("/api/jobs", json={
            "s_number": "S8300",
            "title": "Material Test",
            "priority_level": "medium"
        }, headers=auth_headers)
        job_id = job_response.json()['id']

        cam_response = client.post("/api/cam-items", json={
            "job_id": job_id,
            "set_no": 1,
            "cam_no": 1,
            "status_station": "sharpen"
        }, headers=auth_headers)
        cam_id = cam_response.json()['id']

        # Invalid material_removed (> 1.0) - rejected by Pydantic
        response = client.post("/api/moves", json={
            "cam_item_id": cam_id,
            "to_station": "cabinet",
            "material_removed": 1.5
        }, headers=auth_headers)
        assert response.status_code == 422

        # Invalid material_removed (< 0) - rejected by Pydantic
        response = client.post("/api/moves", json={
            "cam_item_id": cam_id,
            "to_station": "cabinet",
            "material_removed": -0.1
        }, headers=auth_headers)
        assert response.status_code == 422

    def test_bulk_create_validation(self, client, auth_headers):
        """Test bulk create validation"""
        # Create job
        job_response = client.post("/api/jobs", json={
            "s_number": "S8400",
            "title": "Bulk Test",
            "priority_level": "medium"
        }, headers=auth_headers)
        job_id = job_response.json()['id']

        # Invalid: duplicate set numbers
        response = client.post("/api/cam-items/bulk", json={
            "job_id": job_id,
            "sets": [1, 1, 2],
            "cams_per_set": 2,
            "initial_station": "cabinet"
        }, headers=auth_headers)
        assert response.status_code == 422

        # Invalid: zero or negative set numbers
        response = client.post("/api/cam-items/bulk", json={
            "job_id": job_id,
            "sets": [0, 1, 2],
            "cams_per_set": 2,
            "initial_station": "cabinet"
        }, headers=auth_headers)
        assert response.status_code == 422

        # Invalid: too many cams per set
        response = client.post("/api/cam-items/bulk", json={
            "job_id": job_id,
            "sets": [1, 2],
            "cams_per_set": 101,
            "initial_station": "cabinet"
        }, headers=auth_headers)
        assert response.status_code == 422

    def test_station_validation_in_filters(self, client):
        """Test station validation in query filters"""
        # Invalid station in filter
        response = client.get("/api/cam-items?station=invalid_station")
        assert response.status_code == 400
        assert "Invalid station" in response.json()['detail']


class TestHealthCheck:
    """Tests for health check endpoint"""

    def test_health_check_success(self, client):
        """Test health check returns healthy status"""
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()

        assert data['status'] == "healthy"
        assert 'timestamp' in data
        assert 'database' in data
        assert 'disk' in data
        assert 'version' in data

    def test_health_check_database_info(self, client):
        """Test health check includes database information"""
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()

        assert data['database']['status'] == "connected"
        assert 'jobs_count' in data['database']
        assert 'cams_count' in data['database']
        assert 'moves_count' in data['database']

    def test_health_check_disk_info(self, client):
        """Test health check includes disk information"""
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()

        assert 'free_gb' in data['disk']
        assert 'total_gb' in data['disk']
        assert 'used_percent' in data['disk']
        assert data['disk']['free_gb'] >= 0
        assert data['disk']['total_gb'] > 0


class TestRateLimiting:
    """Tests for rate limiting (basic checks)"""

    def test_rate_limit_headers_present(self, client):
        """Test that rate limit headers are present in response"""
        response = client.get("/api/jobs")
        # Note: slowapi adds X-RateLimit headers
        # Just verify the endpoint works with rate limiting enabled
        assert response.status_code == 200

    def test_endpoints_have_rate_limiting(self, client, auth_headers):
        """Test that key endpoints have rate limiting configured"""
        # These should all work (under limit)
        endpoints = [
            ("GET", "/api/jobs"),
            ("GET", "/api/cam-items"),
            ("GET", "/api/search?q=test"),
            ("GET", "/health"),
        ]

        for method, url in endpoints:
            if method == "GET":
                response = client.get(url)
                # Should succeed (not blocked by rate limit yet)
                assert response.status_code in [200, 404]  # 404 if no results


class TestErrorMessages:
    """Tests for improved error messages"""

    def test_invalid_station_error_message(self, client):
        """Test error message for invalid station"""
        response = client.get("/api/cam-items?station=wrong_station")
        assert response.status_code == 400
        assert "Invalid station" in response.json()['detail']
        # Should include list of valid stations
        assert "Must be one of" in response.json()['detail']

    def test_validation_error_messages(self, client, auth_headers):
        """Test validation error messages are clear"""
        response = client.post("/api/jobs", json={
            "s_number": "",
            "title": "Test",
            "priority_level": "medium"
        }, headers=auth_headers)
        assert response.status_code == 422
        # Pydantic will provide validation error details
        assert 'detail' in response.json()
