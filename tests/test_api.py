"""
Tests for API endpoints
"""
import pytest
from fastapi.testclient import TestClient


class TestAuthentication:
    """Tests for HTTP Basic Auth"""

    def test_admin_endpoint_without_auth_fails(self, client):
        """Test that admin endpoints require authentication"""
        response = client.post("/api/jobs", json={
            "s_number": "S1234",
            "title": "Test Job",
            "priority_level": "medium"
        })
        assert response.status_code == 401

    def test_admin_endpoint_with_invalid_auth_fails(self, client):
        """Test that invalid credentials are rejected"""
        import base64
        credentials = base64.b64encode(b"wrong:wrong").decode("utf-8")
        headers = {"Authorization": f"Basic {credentials}"}

        response = client.post("/api/jobs", json={
            "s_number": "S1234",
            "title": "Test Job",
            "priority_level": "medium"
        }, headers=headers)
        assert response.status_code == 401

    def test_admin_endpoint_with_valid_auth_succeeds(self, client, auth_headers):
        """Test that valid credentials work"""
        response = client.post("/api/jobs", json={
            "s_number": "S1234",
            "title": "Test Job",
            "priority_level": "medium"
        }, headers=auth_headers)
        # Should succeed (201 or 200) or fail with different error
        assert response.status_code != 401


class TestJobsAPI:
    """Tests for Jobs API endpoints"""

    def test_list_jobs(self, client):
        """Test listing jobs (public endpoint)"""
        response = client.get("/api/jobs")
        assert response.status_code == 200
        assert isinstance(response.json(), list)

    def test_create_job(self, client, auth_headers):
        """Test creating a new job"""
        response = client.post("/api/jobs", json={
            "s_number": "S1234",
            "title": "Test Job",
            "priority_level": "high"
        }, headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert data['s_number'] == "S1234"
        assert data['title'] == "Test Job"
        assert data['priority_level'] == "high"

    def test_create_job_with_duplicate_s_number_fails(self, client, auth_headers):
        """Test that duplicate S-numbers are rejected"""
        # Create first job
        client.post("/api/jobs", json={
            "s_number": "S1234",
            "title": "Test Job",
            "priority_level": "medium"
        }, headers=auth_headers)

        # Try to create duplicate
        response = client.post("/api/jobs", json={
            "s_number": "S1234",
            "title": "Duplicate Job",
            "priority_level": "high"
        }, headers=auth_headers)
        assert response.status_code == 400
        assert "already exists" in response.json()['detail']

    def test_create_job_with_invalid_priority_fails(self, client, auth_headers):
        """Test that invalid priority levels are rejected"""
        response = client.post("/api/jobs", json={
            "s_number": "S1235",
            "title": "Test Job",
            "priority_level": "invalid_priority"
        }, headers=auth_headers)
        assert response.status_code == 400

    def test_update_job(self, client, auth_headers):
        """Test updating a job"""
        # Create job
        response = client.post("/api/jobs", json={
            "s_number": "S1236",
            "title": "Original Title",
            "priority_level": "low"
        }, headers=auth_headers)
        job_id = response.json()['id']

        # Update job
        response = client.patch(f"/api/jobs/{job_id}", json={
            "title": "Updated Title",
            "priority_level": "urgent"
        }, headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert data['title'] == "Updated Title"
        assert data['priority_level'] == "urgent"

    def test_delete_job(self, client, auth_headers):
        """Test deleting a job"""
        # Create job
        response = client.post("/api/jobs", json={
            "s_number": "S1237",
            "title": "To Be Deleted",
            "priority_level": "low"
        }, headers=auth_headers)
        job_id = response.json()['id']

        # Delete job
        response = client.delete(f"/api/jobs/{job_id}", headers=auth_headers)
        assert response.status_code == 200
        assert response.json()['success'] is True

        # Verify job is gone
        response = client.get(f"/api/jobs/{job_id}")
        assert response.status_code == 404


class TestMovesAPI:
    """Tests for move operations"""

    def test_move_cam_to_station(self, client, auth_headers):
        """Test moving a CAM item to a different station"""
        # Create job
        job_response = client.post("/api/jobs", json={
            "s_number": "S2000",
            "title": "Move Test Job",
            "priority_level": "medium"
        }, headers=auth_headers)
        job_id = job_response.json()['id']

        # Create CAM item
        cam_response = client.post("/api/cam-items", json={
            "job_id": job_id,
            "set_no": 1,
            "cam_no": 1,
            "status_station": "cabinet"
        }, headers=auth_headers)
        cam_id = cam_response.json()['id']

        # Move CAM
        response = client.post("/api/moves", json={
            "cam_item_id": cam_id,
            "to_station": "sharpen",
            "operator": "test_user"
        })
        assert response.status_code == 200
        data = response.json()
        assert data['success'] is True
        assert data['from_station'] == "cabinet"
        assert data['to_station'] == "sharpen"

    def test_move_to_same_station_fails(self, client, auth_headers):
        """Test that moving to the same station fails"""
        # Create job and CAM
        job_response = client.post("/api/jobs", json={
            "s_number": "S2001",
            "title": "Same Station Test",
            "priority_level": "medium"
        }, headers=auth_headers)
        job_id = job_response.json()['id']

        cam_response = client.post("/api/cam-items", json={
            "job_id": job_id,
            "set_no": 1,
            "cam_no": 1,
            "status_station": "cabinet"
        }, headers=auth_headers)
        cam_id = cam_response.json()['id']

        # Try to move to same station
        response = client.post("/api/moves", json={
            "cam_item_id": cam_id,
            "to_station": "cabinet"
        })
        assert response.status_code == 400
        assert "already in" in response.json()['detail']

    def test_undo_move(self, client, auth_headers):
        """Test undoing a move"""
        # Create job and CAM
        job_response = client.post("/api/jobs", json={
            "s_number": "S2002",
            "title": "Undo Test",
            "priority_level": "medium"
        }, headers=auth_headers)
        job_id = job_response.json()['id']

        cam_response = client.post("/api/cam-items", json={
            "job_id": job_id,
            "set_no": 1,
            "cam_no": 1,
            "status_station": "cabinet"
        }, headers=auth_headers)
        cam_id = cam_response.json()['id']

        # Move CAM
        client.post("/api/moves", json={
            "cam_item_id": cam_id,
            "to_station": "sharpen"
        })

        # Undo move
        response = client.post(f"/api/moves/undo/{cam_id}")
        assert response.status_code == 200
        data = response.json()
        assert data['success'] is True
        assert data['reverted_to_station'] == "cabinet"


class TestSearchAPI:
    """Tests for search functionality"""

    def test_search_by_s_number(self, client, auth_headers):
        """Test searching by S-number"""
        # Create test job
        client.post("/api/jobs", json={
            "s_number": "S3000",
            "title": "Search Test Job",
            "priority_level": "medium"
        }, headers=auth_headers)

        # Search
        response = client.get("/api/search?q=3000")
        assert response.status_code == 200
        data = response.json()
        assert len(data['jobs']) > 0
        assert any(job['s_number'] == "S3000" for job in data['jobs'])

    def test_search_returns_empty_for_no_match(self, client):
        """Test that search returns empty results for no match"""
        response = client.get("/api/search?q=NONEXISTENT9999")
        assert response.status_code == 200
        data = response.json()
        assert len(data['jobs']) == 0


class TestConfigAPI:
    """Tests for configuration endpoints"""

    def test_get_config_is_public(self, client):
        """Test that getting config doesn't require auth"""
        response = client.get("/api/config")
        assert response.status_code == 200

    def test_update_config_requires_auth(self, client):
        """Test that updating config requires authentication"""
        response = client.patch("/api/config", json={
            "auto_bump_enabled": True
        })
        assert response.status_code == 401

    def test_update_config_with_auth(self, client, auth_headers):
        """Test updating config with authentication"""
        response = client.patch("/api/config", json={
            "auto_bump_enabled": True
        }, headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert data['auto_bump_enabled'] is True


class TestValidation:
    """Tests for input validation"""

    def test_material_removed_validation(self, client, auth_headers):
        """Test that material_removed is validated correctly"""
        # Create job and CAM
        job_response = client.post("/api/jobs", json={
            "s_number": "S4000",
            "title": "Validation Test",
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

        # Try to move from sharpen to cabinet without material_removed
        response = client.post("/api/moves", json={
            "cam_item_id": cam_id,
            "to_station": "cabinet"
        })
        assert response.status_code == 400
        assert "material removed must be specified" in response.json()['detail'].lower()

    def test_invalid_station_rejected(self, client, auth_headers):
        """Test that invalid stations are rejected"""
        job_response = client.post("/api/jobs", json={
            "s_number": "S4001",
            "title": "Invalid Station Test",
            "priority_level": "medium"
        }, headers=auth_headers)
        job_id = job_response.json()['id']

        response = client.post("/api/cam-items", json={
            "job_id": job_id,
            "set_no": 1,
            "cam_no": 1,
            "status_station": "invalid_station"
        }, headers=auth_headers)
        assert response.status_code == 400
