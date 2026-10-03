"""
Tests for set moves, aggregated stats endpoints, exports and migrations.
"""
import csv
import io
import sqlite3

import pytest

from database import migrate_database


def _create_job(client, headers, s_number="S5000", title="Workflow Job"):
    response = client.post("/api/jobs", json={"s_number": s_number, "title": title}, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def _bulk(client, headers, job_id, num_sets, cams):
    response = client.post("/api/cam-items/bulk", json={
        "job_id": job_id, "num_sets": num_sets, "cams": cams
    }, headers=headers)
    assert response.status_code == 200, response.text
    return {(i["set_no"], i["cam_no"]): i["id"] for i in response.json()["items"]}


def _station(client, cam_id):
    return client.get(f"/api/cam-items/{cam_id}").json()["cam_item"]["status_station"]


def _move(client, headers, cam_id, station, material=None):
    body = {"cam_item_id": cam_id, "to_station": station}
    if material is not None:
        body["material_removed"] = material
    response = client.post("/api/moves", json=body, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


TWO_POSITION_CAMS = [
    {"cam_no": 1, "die_position": "upper"},
    {"cam_no": 2, "die_position": "lower"},
]


class TestSetMoves:

    def test_move_set_moves_every_cam(self, client, auth_headers):
        job = _create_job(client, auth_headers)
        ids = _bulk(client, auth_headers, job["id"], 2, TWO_POSITION_CAMS)

        response = client.post("/api/moves/set", json={
            "job_id": job["id"], "set_no": 1, "to_station": "sharpen"
        }, headers=auth_headers)
        assert response.status_code == 200, response.text
        data = response.json()
        assert len(data["moved"]) == 2
        assert data["bumped"] == []
        assert _station(client, ids[(1, 1)]) == "sharpen"
        assert _station(client, ids[(1, 2)]) == "sharpen"
        assert _station(client, ids[(2, 1)]) == "cabinet"

    def test_move_set_to_active_bumps_conflicting_set(self, client, auth_headers):
        job = _create_job(client, auth_headers)
        ids = _bulk(client, auth_headers, job["id"], 2, TWO_POSITION_CAMS)
        client.post("/api/moves/set", json={"job_id": job["id"], "set_no": 1, "to_station": "active"},
                    headers=auth_headers)

        response = client.post("/api/moves/set", json={
            "job_id": job["id"], "set_no": 2, "to_station": "active"
        }, headers=auth_headers)
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["bumped_sets"] == [1]
        assert len(data["bumped"]) == 2
        assert _station(client, ids[(1, 1)]) == "sharpen"
        assert _station(client, ids[(1, 2)]) == "sharpen"
        assert _station(client, ids[(2, 1)]) == "active"

    def test_move_set_requires_material_for_every_sharpened_cam(self, client, auth_headers):
        job = _create_job(client, auth_headers)
        ids = _bulk(client, auth_headers, job["id"], 1, TWO_POSITION_CAMS)
        client.post("/api/moves/set", json={"job_id": job["id"], "set_no": 1, "to_station": "sharpen"},
                    headers=auth_headers)

        response = client.post("/api/moves/set", json={
            "job_id": job["id"], "set_no": 1, "to_station": "cabinet",
            "material_removed": {str(ids[(1, 1)]): 0.01}
        }, headers=auth_headers)
        assert response.status_code == 400
        assert "Cam 2" in response.json()["detail"]
        # Nothing moved: the set move is all-or-nothing
        assert _station(client, ids[(1, 1)]) == "sharpen"

        response = client.post("/api/moves/set", json={
            "job_id": job["id"], "set_no": 1, "to_station": "cabinet",
            "material_removed": {str(ids[(1, 1)]): 0.01, str(ids[(1, 2)]): 0.02}
        }, headers=auth_headers)
        assert response.status_code == 200, response.text
        assert _station(client, ids[(1, 2)]) == "cabinet"

    def test_move_set_already_there_fails(self, client, auth_headers):
        job = _create_job(client, auth_headers)
        _bulk(client, auth_headers, job["id"], 1, TWO_POSITION_CAMS)
        response = client.post("/api/moves/set", json={
            "job_id": job["id"], "set_no": 1, "to_station": "cabinet"
        }, headers=auth_headers)
        assert response.status_code == 400
        assert "already in" in response.json()["detail"]

    def test_move_set_unknown_set_is_404(self, client, auth_headers):
        job = _create_job(client, auth_headers)
        response = client.post("/api/moves/set", json={
            "job_id": job["id"], "set_no": 9, "to_station": "sharpen"
        }, headers=auth_headers)
        assert response.status_code == 404

    def test_move_set_requires_login(self, client):
        response = client.post("/api/moves/set", json={"job_id": 1, "set_no": 1, "to_station": "sharpen"})
        assert response.status_code == 401


class TestMoveErrors:

    def test_move_unknown_cam_is_404(self, client, auth_headers):
        response = client.post("/api/moves", json={"cam_item_id": 99999, "to_station": "active"},
                               headers=auth_headers)
        assert response.status_code == 404

    def test_zero_material_sharpen_counts_and_undoes(self, client, auth_headers):
        job = _create_job(client, auth_headers)
        ids = _bulk(client, auth_headers, job["id"], 1, [{"cam_no": 1}])
        cam_id = ids[(1, 1)]
        _move(client, auth_headers, cam_id, "sharpen")
        _move(client, auth_headers, cam_id, "cabinet", material=0.0)

        lifespan = client.get(f"/api/cam-items/{cam_id}/lifespan").json()
        assert lifespan["current_lifespan"]["sharpen_count"] == 1

        client.post(f"/api/moves/undo/{cam_id}", headers=auth_headers)
        lifespan = client.get(f"/api/cam-items/{cam_id}/lifespan").json()
        assert lifespan["current_lifespan"]["sharpen_count"] == 0


class TestAggregatedStats:

    def _sharpened_job(self, client, headers):
        job = _create_job(client, headers)
        ids = _bulk(client, headers, job["id"], 1, [{"cam_no": 1}, {"cam_no": 2}])
        cam_id = ids[(1, 1)]
        for material in (0.010, 0.020):
            _move(client, headers, cam_id, "sharpen")
            _move(client, headers, cam_id, "cabinet", material=material)
        return job, ids

    def test_job_tool_stats(self, client, auth_headers):
        job, ids = self._sharpened_job(client, auth_headers)
        response = client.get(f"/api/jobs/{job['id']}/tool-stats")
        assert response.status_code == 200
        stats = {s["cam_item_id"]: s for s in response.json()}
        assert len(stats) == 2

        sharpened = stats[ids[(1, 1)]]
        assert sharpened["sharpen_count"] == 2
        assert sharpened["last_material_removed"] == pytest.approx(0.020)
        assert sharpened["avg_material_removed"] == pytest.approx(0.015)
        assert sharpened["lifespan_material_removed"] == pytest.approx(0.030)
        assert sharpened["material_remaining"] == pytest.approx(0.345)
        assert sharpened["percent_life_used"] == pytest.approx(8.0)

        untouched = stats[ids[(1, 2)]]
        assert untouched["sharpen_count"] == 0
        assert untouched["last_material_removed"] is None

    def test_job_tool_stats_unknown_job(self, client):
        assert client.get("/api/jobs/99999/tool-stats").status_code == 404

    def test_sharpen_stats_matches_tool_stats(self, client, auth_headers):
        _, ids = self._sharpened_job(client, auth_headers)
        data = client.get(f"/api/cam-items/{ids[(1, 1)]}/sharpen-stats").json()
        assert data["sharpen_count"] == 2
        assert data["last_material_removed"] == pytest.approx(0.020)

    def test_material_stats_only_lists_sharpened_tools(self, client, auth_headers):
        job, ids = self._sharpened_job(client, auth_headers)
        data = client.get("/api/analytics/material-stats").json()
        assert [d["cam_item_id"] for d in data] == [ids[(1, 1)]]
        assert data[0]["s_number"] == job["s_number"]

    def test_moves_list_includes_s_number(self, client, auth_headers):
        job, _ = self._sharpened_job(client, auth_headers)
        moves = client.get("/api/moves?limit=3").json()
        assert len(moves) == 3
        assert all(m["s_number"] == job["s_number"] for m in moves)

    def test_search_tools_include_s_number(self, client, auth_headers):
        job, _ = self._sharpened_job(client, auth_headers)
        data = client.get(f"/api/search?q={job['s_number']}-1-1").json()
        assert len(data["cam_items"]) == 1
        assert data["cam_items"][0]["s_number"] == job["s_number"]


class TestExports:

    def _rows(self, response):
        assert response.status_code == 200, response.text
        return list(csv.reader(io.StringIO(response.text)))

    def test_cam_items_csv_contents(self, client, auth_headers):
        job = _create_job(client, auth_headers)
        _bulk(client, auth_headers, job["id"], 2, [{"cam_no": 1}])
        rows = self._rows(client.get("/api/export/cam-items/csv", headers=auth_headers))
        assert rows[0][:3] == ["s_number", "set_no", "cam_no"]
        assert [r[:3] for r in rows[1:]] == [[job["s_number"], "1", "1"], [job["s_number"], "2", "1"]]

    def test_tool_summary_csv(self, client, auth_headers):
        job = _create_job(client, auth_headers)
        ids = _bulk(client, auth_headers, job["id"], 1, [{"cam_no": 1}])
        cam_id = ids[(1, 1)]
        _move(client, auth_headers, cam_id, "sharpen")
        _move(client, auth_headers, cam_id, "cabinet", material=0.05)
        _move(client, auth_headers, cam_id, "refill")
        _move(client, auth_headers, cam_id, "cabinet")

        rows = self._rows(client.get("/api/export/tool-summary/csv", headers=auth_headers))
        record = dict(zip(rows[0], rows[1]))
        assert record["current_lifespan_number"] == "2"
        assert record["total_refills"] == "1"
        assert record["total_sharpenings"] == "1"
        assert float(record["total_material_removed"]) == pytest.approx(0.05)
        assert record["current_cycle_sharpenings"] == "0"

    def test_priority_changes_csv_requires_admin(self, client, user_auth_headers):
        assert client.get("/api/export/priority-changes/csv", headers=user_auth_headers).status_code == 403


class TestMigration:

    def test_migrates_legacy_schema(self, tmp_path):
        """A first-release database gains new columns, tables and backfilled lifespans."""
        path = str(tmp_path / "legacy.db")
        conn = sqlite3.connect(path)
        conn.executescript("""
            CREATE TABLE jobs (id INTEGER PRIMARY KEY, s_number TEXT UNIQUE, title TEXT,
                               priority_base INTEGER DEFAULT 0, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                               notes TEXT);
            CREATE TABLE cam_items (id INTEGER PRIMARY KEY, job_id INTEGER, set_no INTEGER, cam_no INTEGER,
                                    enter_die_steel TEXT, exit_die_steel TEXT, status_station TEXT,
                                    status_updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, notes TEXT,
                                    eol_cycles_expected INTEGER, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE moves (id INTEGER PRIMARY KEY, cam_item_id INTEGER, from_station TEXT, to_station TEXT,
                                moved_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, operator TEXT, notes TEXT,
                                undone BOOLEAN DEFAULT 0);
            CREATE TABLE config (key TEXT PRIMARY KEY, value TEXT NOT NULL,
                                 updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
            INSERT INTO jobs (id, s_number, priority_base) VALUES (1, '100', 2);
            INSERT INTO cam_items (id, job_id, set_no, cam_no, status_station) VALUES (1, 1, 1, 1, 'cabinet');
            INSERT INTO moves (cam_item_id, from_station, to_station, moved_at)
                VALUES (1, 'new', 'cabinet', '2024-01-01 00:00:00');
        """)
        conn.commit()
        conn.close()

        migrate_database(path)
        migrate_database(path)  # idempotent

        conn = sqlite3.connect(path)
        assert conn.execute("SELECT priority_level FROM jobs WHERE id = 1").fetchone()[0] == "high"
        cam_columns = {r[1] for r in conn.execute("PRAGMA table_info(cam_items)")}
        assert {"die_position", "max_material_life"} <= cam_columns
        assert "material_removed" in {r[1] for r in conn.execute("PRAGMA table_info(moves)")}
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"users", "sessions", "tool_lifespans", "priority_changes"} <= tables
        assert conn.execute("SELECT COUNT(*) FROM tool_lifespans WHERE ended_at IS NULL").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM users WHERE role = 'admin'").fetchone()[0] == 1
        conn.close()
