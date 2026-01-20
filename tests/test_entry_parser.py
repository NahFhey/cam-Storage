"""
Tests for entry_parser module
"""
import pytest
from entry_parser import parse_manual_entry, ParsedEntry


class TestParseManualEntry:
    """Tests for manual entry parsing"""

    def test_parse_full_format_with_s_prefix(self):
        """Test parsing 'S1793 SET1 CAM2' format"""
        result = parse_manual_entry("S1793 SET1 CAM2")
        assert result.s_number == "1793"
        assert result.set_no == 1
        assert result.cam_no == 2
        assert result.confidence == "exact"

    def test_parse_full_format_without_s_prefix(self):
        """Test parsing '1793 SET1 CAM2' format"""
        result = parse_manual_entry("1793 SET1 CAM2")
        assert result.s_number == "1793"
        assert result.set_no == 1
        assert result.cam_no == 2
        assert result.confidence == "exact"

    def test_parse_compact_format_with_dashes(self):
        """Test parsing '1793-1-2' format"""
        result = parse_manual_entry("1793-1-2")
        assert result.s_number == "1793"
        assert result.set_no == 1
        assert result.cam_no == 2
        assert result.confidence == "exact"

    def test_parse_compact_format_with_spaces(self):
        """Test parsing '1793 1 2' format"""
        result = parse_manual_entry("1793 1 2")
        assert result.s_number == "1793"
        assert result.set_no == 1
        assert result.cam_no == 2
        assert result.confidence == "exact"

    def test_parse_mixed_format(self):
        """Test parsing 'S1793-C2-SET1' format"""
        result = parse_manual_entry("S1793-C2-SET1")
        assert result.s_number == "1793"
        assert result.set_no == 1
        assert result.cam_no == 2
        assert result.confidence == "exact"

    def test_parse_set_only(self):
        """Test parsing with set number only"""
        result = parse_manual_entry("S1793 SET1")
        assert result.s_number == "1793"
        assert result.set_no == 1
        assert result.cam_no is None
        assert result.confidence == "partial"

    def test_parse_cam_only(self):
        """Test parsing with cam number only"""
        result = parse_manual_entry("S1793 CAM2")
        assert result.s_number == "1793"
        assert result.set_no is None
        assert result.cam_no == 2
        assert result.confidence == "partial"

    def test_parse_s_number_only(self):
        """Test parsing with just S-number"""
        result = parse_manual_entry("S1793")
        assert result.s_number == "1793"
        assert result.set_no is None
        assert result.cam_no is None
        assert result.confidence == "partial"

    def test_parse_with_extra_whitespace(self):
        """Test parsing with extra whitespace"""
        result = parse_manual_entry("  S1793   SET1   CAM2  ")
        assert result.s_number == "1793"
        assert result.set_no == 1
        assert result.cam_no == 2
        assert result.confidence == "exact"

    def test_parse_case_insensitive(self):
        """Test parsing is case insensitive"""
        result = parse_manual_entry("s1793 set1 cam2")
        assert result.s_number == "1793"
        assert result.set_no == 1
        assert result.cam_no == 2
        assert result.confidence == "exact"

    def test_parse_invalid_entry(self):
        """Test parsing invalid entry raises ValueError"""
        with pytest.raises(ValueError, match="Unable to parse entry"):
            parse_manual_entry("invalid entry xyz")

    def test_parse_with_special_characters(self):
        """Test parsing with special characters (should be ignored)"""
        result = parse_manual_entry("S1793-SET1/CAM2")
        assert result.s_number == "1793"
        assert result.set_no == 1
        assert result.cam_no == 2


@pytest.mark.asyncio
class TestResolveEntry:
    """Tests for entry resolution (requires database)"""

    async def test_resolve_exact_match(self, test_db):
        """Test resolving entry with exact match"""
        from entry_parser import resolve_entry

        # Create test job
        cursor = await test_db.execute(
            "INSERT INTO jobs (s_number, title, priority_level) VALUES (?, ?, ?)",
            ("1793", "Test Job", "medium")
        )
        job_id = cursor.lastrowid

        # Create test cam item
        await test_db.execute(
            "INSERT INTO cam_items (job_id, set_no, cam_no, status_station) VALUES (?, ?, ?, ?)",
            (job_id, 1, 2, "cabinet")
        )
        await test_db.commit()

        # Resolve entry
        result = await resolve_entry(test_db, "S1793 SET1 CAM2")

        assert result['status'] == 'exact'
        assert result['cam_item']['set_no'] == 1
        assert result['cam_item']['cam_no'] == 2
        assert result['job']['s_number'] == "1793"

    async def test_resolve_job_not_found(self, test_db):
        """Test resolving entry for non-existent job"""
        from entry_parser import resolve_entry

        result = await resolve_entry(test_db, "S9999")

        assert result['status'] == 'job_not_found'
        assert result['s_number'] == "9999"

    async def test_resolve_multiple_candidates(self, test_db):
        """Test resolving entry with multiple matches"""
        from entry_parser import resolve_entry

        # Create test job
        cursor = await test_db.execute(
            "INSERT INTO jobs (s_number, title, priority_level) VALUES (?, ?, ?)",
            ("1793", "Test Job", "medium")
        )
        job_id = cursor.lastrowid

        # Create multiple cam items in same set
        await test_db.execute(
            "INSERT INTO cam_items (job_id, set_no, cam_no, status_station) VALUES (?, ?, ?, ?)",
            (job_id, 1, 1, "cabinet")
        )
        await test_db.execute(
            "INSERT INTO cam_items (job_id, set_no, cam_no, status_station) VALUES (?, ?, ?, ?)",
            (job_id, 1, 2, "cabinet")
        )
        await test_db.commit()

        # Resolve entry with only set number
        result = await resolve_entry(test_db, "S1793 SET1")

        assert result['status'] == 'multiple'
        assert len(result['candidates']) == 2

    async def test_resolve_cam_not_found(self, test_db):
        """Test resolving entry for non-existent cam"""
        from entry_parser import resolve_entry

        # Create test job
        cursor = await test_db.execute(
            "INSERT INTO jobs (s_number, title, priority_level) VALUES (?, ?, ?)",
            ("1793", "Test Job", "medium")
        )
        await test_db.commit()

        # Try to resolve non-existent cam
        result = await resolve_entry(test_db, "S1793 SET1 CAM2")

        assert result['status'] == 'not_found'
        assert 'CAM item not found' in result['message']
