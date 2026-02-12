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

    async def test_hot_list_blocked_position_all_refill(self, test_db):
        """Test that a job gets has_blocked_position=True when all instances of a cam_no are in refill"""
        from business_logic import generate_hot_list

        # Create job with 3 sets, each with 2 cams (cam_no 1=upper, cam_no 2=lower)
        cursor = await test_db.execute(
            "INSERT INTO jobs (s_number, title, priority_level) VALUES (?, ?, ?)",
            ("4000", "Blocked Position Job", "low")
        )
        job_id = cursor.lastrowid

        # Set 1: cam1=sharpen, cam2=refill
        await test_db.execute(
            "INSERT INTO cam_items (job_id, set_no, cam_no, die_position, status_station) VALUES (?, ?, ?, ?, ?)",
            (job_id, 1, 1, "upper", "sharpen")
        )
        await test_db.execute(
            "INSERT INTO cam_items (job_id, set_no, cam_no, die_position, status_station) VALUES (?, ?, ?, ?, ?)",
            (job_id, 1, 2, "lower", "refill")
        )
        # Set 2: cam1=active, cam2=refill
        await test_db.execute(
            "INSERT INTO cam_items (job_id, set_no, cam_no, die_position, status_station) VALUES (?, ?, ?, ?, ?)",
            (job_id, 2, 1, "upper", "active")
        )
        await test_db.execute(
            "INSERT INTO cam_items (job_id, set_no, cam_no, die_position, status_station) VALUES (?, ?, ?, ?, ?)",
            (job_id, 2, 2, "lower", "refill")
        )
        # Set 3: cam1=cabinet, cam2=refill
        await test_db.execute(
            "INSERT INTO cam_items (job_id, set_no, cam_no, die_position, status_station) VALUES (?, ?, ?, ?, ?)",
            (job_id, 3, 1, "upper", "cabinet")
        )
        await test_db.execute(
            "INSERT INTO cam_items (job_id, set_no, cam_no, die_position, status_station) VALUES (?, ?, ?, ?, ?)",
            (job_id, 3, 2, "lower", "refill")
        )
        await test_db.commit()

        hot_list = await generate_hot_list(test_db)
        job_entry = next(item for item in hot_list if item['s_number'] == "4000")

        # cam_no 2 is in refill across all sets => blocked
        assert job_entry['has_blocked_position'] is True
        # Priority should include the +2 blocked position boost (base 0 + blocked 2 = at least 2)
        assert job_entry['priority_score'] >= 2

    async def test_hot_list_blocked_position_all_sharpen(self, test_db):
        """Test that a job gets has_blocked_position=True when all instances of a cam_no are in sharpen"""
        from business_logic import generate_hot_list

        cursor = await test_db.execute(
            "INSERT INTO jobs (s_number, title, priority_level) VALUES (?, ?, ?)",
            ("4010", "Blocked Sharpen Job", "low")
        )
        job_id = cursor.lastrowid

        # Set 1: cam1=cabinet, cam2=sharpen
        await test_db.execute(
            "INSERT INTO cam_items (job_id, set_no, cam_no, die_position, status_station) VALUES (?, ?, ?, ?, ?)",
            (job_id, 1, 1, "upper", "cabinet")
        )
        await test_db.execute(
            "INSERT INTO cam_items (job_id, set_no, cam_no, die_position, status_station) VALUES (?, ?, ?, ?, ?)",
            (job_id, 1, 2, "lower", "sharpen")
        )
        # Set 2: cam1=active, cam2=sharpen
        await test_db.execute(
            "INSERT INTO cam_items (job_id, set_no, cam_no, die_position, status_station) VALUES (?, ?, ?, ?, ?)",
            (job_id, 2, 1, "upper", "active")
        )
        await test_db.execute(
            "INSERT INTO cam_items (job_id, set_no, cam_no, die_position, status_station) VALUES (?, ?, ?, ?, ?)",
            (job_id, 2, 2, "lower", "sharpen")
        )
        await test_db.commit()

        hot_list = await generate_hot_list(test_db)
        job_entry = next(item for item in hot_list if item['s_number'] == "4010")

        # cam_no 2 is in sharpen across all sets => blocked
        assert job_entry['has_blocked_position'] is True
        assert job_entry['priority_score'] >= 2

    async def test_hot_list_no_blocked_position_when_one_available(self, test_db):
        """Test that has_blocked_position=False when every cam_no has at least one available instance"""
        from business_logic import generate_hot_list

        cursor = await test_db.execute(
            "INSERT INTO jobs (s_number, title, priority_level) VALUES (?, ?, ?)",
            ("4001", "Not Blocked Job", "low")
        )
        job_id = cursor.lastrowid

        # Set 1: cam1=sharpen, cam2=cabinet (cam2 has one available)
        await test_db.execute(
            "INSERT INTO cam_items (job_id, set_no, cam_no, die_position, status_station) VALUES (?, ?, ?, ?, ?)",
            (job_id, 1, 1, "upper", "sharpen")
        )
        await test_db.execute(
            "INSERT INTO cam_items (job_id, set_no, cam_no, die_position, status_station) VALUES (?, ?, ?, ?, ?)",
            (job_id, 1, 2, "lower", "cabinet")
        )
        # Set 2: cam1=active, cam2=refill
        await test_db.execute(
            "INSERT INTO cam_items (job_id, set_no, cam_no, die_position, status_station) VALUES (?, ?, ?, ?, ?)",
            (job_id, 2, 1, "upper", "active")
        )
        await test_db.execute(
            "INSERT INTO cam_items (job_id, set_no, cam_no, die_position, status_station) VALUES (?, ?, ?, ?, ?)",
            (job_id, 2, 2, "lower", "refill")
        )
        await test_db.commit()

        hot_list = await generate_hot_list(test_db)
        job_entry = next(item for item in hot_list if item['s_number'] == "4001")

        # cam1 has active in set 2, cam2 has cabinet in set 1 => not blocked
        assert job_entry['has_blocked_position'] is False

    async def test_hot_list_available_sets_excludes_sharpen(self, test_db):
        """Test that available_sets excludes sets where all cams are in sharpen or refill"""
        from business_logic import generate_hot_list

        cursor = await test_db.execute(
            "INSERT INTO jobs (s_number, title, priority_level) VALUES (?, ?, ?)",
            ("4002", "Sharpen Excluded Job", "low")
        )
        job_id = cursor.lastrowid

        # Set 1: all sharpen (should NOT count as available)
        await test_db.execute(
            "INSERT INTO cam_items (job_id, set_no, cam_no, status_station) VALUES (?, ?, ?, ?)",
            (job_id, 1, 1, "sharpen")
        )
        # Set 2: one sharpen + one cabinet (should count as available)
        await test_db.execute(
            "INSERT INTO cam_items (job_id, set_no, cam_no, status_station) VALUES (?, ?, ?, ?)",
            (job_id, 2, 1, "sharpen")
        )
        await test_db.execute(
            "INSERT INTO cam_items (job_id, set_no, cam_no, status_station) VALUES (?, ?, ?, ?)",
            (job_id, 2, 2, "cabinet")
        )
        await test_db.commit()

        hot_list = await generate_hot_list(test_db)
        job_entry = next(item for item in hot_list if item['s_number'] == "4002")

        # Only set 2 should be available (has a cabinet cam)
        assert job_entry['available_sets'] == 1

    def test_calculate_priority_blocked_position_boost(self):
        """Test calculate_priority gives +2 for blocked position"""
        from business_logic import calculate_priority

        _, score_without, _ = calculate_priority(
            available_sets=2, refill_count=1, priority_level='low',
            has_blocked_position=False
        )
        _, score_with, _ = calculate_priority(
            available_sets=2, refill_count=1, priority_level='low',
            has_blocked_position=True
        )

        assert score_with == score_without + 2
