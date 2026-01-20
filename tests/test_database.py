"""
Tests for database functionality
"""
import pytest
import sqlite3


class TestDatabaseSchema:
    """Tests for database schema and constraints"""

    def test_database_initializes_correctly(self, test_db_path):
        """Test that database initializes with correct schema"""
        conn = sqlite3.connect(test_db_path)
        cursor = conn.cursor()

        # Check tables exist
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row[0] for row in cursor.fetchall()}

        assert 'jobs' in tables
        assert 'cam_items' in tables
        assert 'moves' in tables
        assert 'config' in tables

        conn.close()

    def test_foreign_keys_are_enabled(self, test_db_path):
        """Test that foreign keys are enforced"""
        conn = sqlite3.connect(test_db_path)
        conn.execute("PRAGMA foreign_keys = ON")
        cursor = conn.cursor()

        # Try to insert cam_item with non-existent job_id
        with pytest.raises(sqlite3.IntegrityError):
            cursor.execute(
                "INSERT INTO cam_items (job_id, set_no, cam_no, status_station) VALUES (?, ?, ?, ?)",
                (99999, 1, 1, "cabinet")
            )
            conn.commit()

        conn.close()

    @pytest.mark.asyncio
    async def test_cascade_delete_works(self, test_db):
        """Test that deleting a job cascades to cam_items and moves"""
        # Create job
        cursor = await test_db.execute(
            "INSERT INTO jobs (s_number, title, priority_level) VALUES (?, ?, ?)",
            ("1000", "Test Job", "medium")
        )
        job_id = cursor.lastrowid

        # Create cam item
        cursor = await test_db.execute(
            "INSERT INTO cam_items (job_id, set_no, cam_no, status_station) VALUES (?, ?, ?, ?)",
            (job_id, 1, 1, "cabinet")
        )
        cam_id = cursor.lastrowid
        await test_db.commit()

        # Verify items exist
        cursor = await test_db.execute("SELECT COUNT(*) FROM cam_items WHERE job_id = ?", (job_id,))
        count = (await cursor.fetchone())[0]
        assert count == 1

        # Delete job
        await test_db.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
        await test_db.commit()

        # Verify cam_items were deleted
        cursor = await test_db.execute("SELECT COUNT(*) FROM cam_items WHERE job_id = ?", (job_id,))
        count = (await cursor.fetchone())[0]
        assert count == 0

    @pytest.mark.asyncio
    async def test_unique_constraint_on_cam_items(self, test_db):
        """Test that job_id+set_no+cam_no must be unique"""
        # Create job
        cursor = await test_db.execute(
            "INSERT INTO jobs (s_number, title, priority_level) VALUES (?, ?, ?)",
            ("1001", "Test Job", "medium")
        )
        job_id = cursor.lastrowid

        # Create cam item
        await test_db.execute(
            "INSERT INTO cam_items (job_id, set_no, cam_no, status_station) VALUES (?, ?, ?, ?)",
            (job_id, 1, 1, "cabinet")
        )
        await test_db.commit()

        # Try to create duplicate
        with pytest.raises(Exception):  # Should raise IntegrityError
            await test_db.execute(
                "INSERT INTO cam_items (job_id, set_no, cam_no, status_station) VALUES (?, ?, ?, ?)",
                (job_id, 1, 1, "sharpen")
            )
            await test_db.commit()

    @pytest.mark.asyncio
    async def test_config_table_works(self, test_db):
        """Test that config table stores and retrieves values"""
        # Insert config value
        await test_db.execute(
            "INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)",
            ("test_key", "test_value")
        )
        await test_db.commit()

        # Retrieve value
        cursor = await test_db.execute("SELECT value FROM config WHERE key = ?", ("test_key",))
        row = await cursor.fetchone()
        assert row[0] == "test_value"


@pytest.mark.asyncio
class TestBusinessLogic:
    """Tests for business logic functions"""

    async def test_move_cam_updates_status(self, test_db):
        """Test that moving a CAM updates its status"""
        from business_logic import move_cam_to_station

        # Create job and cam
        cursor = await test_db.execute(
            "INSERT INTO jobs (s_number, title, priority_level) VALUES (?, ?, ?)",
            ("2000", "Test Job", "medium")
        )
        job_id = cursor.lastrowid

        cursor = await test_db.execute(
            "INSERT INTO cam_items (job_id, set_no, cam_no, status_station) VALUES (?, ?, ?, ?)",
            (job_id, 1, 1, "cabinet")
        )
        cam_id = cursor.lastrowid
        await test_db.commit()

        # Move cam (provide auto_bump explicitly to avoid config lookup)
        result = await move_cam_to_station(test_db, cam_id, "sharpen", auto_bump=False)

        assert result['success'] is True
        assert result['from_station'] == "cabinet"
        assert result['to_station'] == "sharpen"

        # Verify database was updated
        cursor = await test_db.execute(
            "SELECT status_station FROM cam_items WHERE id = ?",
            (cam_id,)
        )
        row = await cursor.fetchone()
        assert row['status_station'] == "sharpen"

    async def test_move_creates_audit_record(self, test_db):
        """Test that moves are recorded in the moves table"""
        from business_logic import move_cam_to_station

        # Create job and cam
        cursor = await test_db.execute(
            "INSERT INTO jobs (s_number, title, priority_level) VALUES (?, ?, ?)",
            ("2001", "Test Job", "medium")
        )
        job_id = cursor.lastrowid

        cursor = await test_db.execute(
            "INSERT INTO cam_items (job_id, set_no, cam_no, status_station) VALUES (?, ?, ?, ?)",
            (job_id, 1, 1, "cabinet")
        )
        cam_id = cursor.lastrowid
        await test_db.commit()

        # Move cam (provide auto_bump explicitly to avoid config lookup)
        result = await move_cam_to_station(test_db, cam_id, "sharpen", operator="test_user", auto_bump=False)

        # Verify move record
        cursor = await test_db.execute(
            "SELECT * FROM moves WHERE cam_item_id = ? AND undone = 0",
            (cam_id,)
        )
        moves = await cursor.fetchall()
        # Should have initial move + this move
        assert len(moves) >= 1

        last_move = moves[-1]
        assert last_move['from_station'] == "cabinet"
        assert last_move['to_station'] == "sharpen"
        assert last_move['operator'] == "test_user"

    async def test_undo_reverts_status(self, test_db):
        """Test that undo reverts CAM status"""
        from business_logic import move_cam_to_station, undo_last_move

        # Create job and cam
        cursor = await test_db.execute(
            "INSERT INTO jobs (s_number, title, priority_level) VALUES (?, ?, ?)",
            ("2002", "Test Job", "medium")
        )
        job_id = cursor.lastrowid

        cursor = await test_db.execute(
            "INSERT INTO cam_items (job_id, set_no, cam_no, status_station) VALUES (?, ?, ?, ?)",
            (job_id, 1, 1, "cabinet")
        )
        cam_id = cursor.lastrowid
        await test_db.commit()

        # Move and then undo (provide auto_bump explicitly to avoid config lookup)
        await move_cam_to_station(test_db, cam_id, "sharpen", auto_bump=False)
        result = await undo_last_move(test_db, cam_id)

        assert result['success'] is True
        assert result['reverted_to_station'] == "cabinet"

        # Verify status is reverted
        cursor = await test_db.execute(
            "SELECT status_station FROM cam_items WHERE id = ?",
            (cam_id,)
        )
        row = await cursor.fetchone()
        assert row['status_station'] == "cabinet"

    async def test_hot_list_excludes_jobs_without_sharpen(self, test_db):
        """Test that hot list only shows jobs with items in sharpen"""
        from business_logic import generate_hot_list

        # Create job with cam in cabinet only
        cursor = await test_db.execute(
            "INSERT INTO jobs (s_number, title, priority_level) VALUES (?, ?, ?)",
            ("3000", "Cabinet Job", "high")
        )
        job1_id = cursor.lastrowid

        await test_db.execute(
            "INSERT INTO cam_items (job_id, set_no, cam_no, status_station) VALUES (?, ?, ?, ?)",
            (job1_id, 1, 1, "cabinet")
        )

        # Create job with cam in sharpen
        cursor = await test_db.execute(
            "INSERT INTO jobs (s_number, title, priority_level) VALUES (?, ?, ?)",
            ("3001", "Sharpen Job", "medium")
        )
        job2_id = cursor.lastrowid

        await test_db.execute(
            "INSERT INTO cam_items (job_id, set_no, cam_no, status_station) VALUES (?, ?, ?, ?)",
            (job2_id, 1, 1, "sharpen")
        )
        await test_db.commit()

        # Generate hot list
        hot_list = await generate_hot_list(test_db)

        # Should only contain job with sharpen items
        s_numbers = [item['s_number'] for item in hot_list]
        assert "3001" in s_numbers
        assert "3000" not in s_numbers
