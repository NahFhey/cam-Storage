"""Rolling life, legacy migration, refill decisions, undo and export regressions."""
import csv
import io
import sqlite3

import pytest

import config
from business_logic import get_lifespan_forecast, open_new_lifespan
from database import init_database, migrate_database


@pytest.fixture
async def life_cam(test_db):
    job = await test_db.execute("INSERT INTO jobs (s_number) VALUES ('9001')")
    cam = await test_db.execute(
        "INSERT INTO cam_items (job_id, set_no, cam_no, status_station) VALUES (?, 1, 1, 'cabinet')", (job.lastrowid,)
    )
    await test_db.commit()
    return cam.lastrowid


async def _cycles(db, cam_id, totals):
    for number, total in enumerate(totals, 1):
        await db.execute(
            """INSERT INTO tool_lifespans
               (cam_item_id, lifespan_number, ended_at, total_material_removed, sharpen_count)
               VALUES (?, ?, '2026-01-01 12:00:00', ?, 10)""", (cam_id, number, total)
        )


async def _expected(db, cam_id):
    # Local import lets the other regression tests run on the phase-1 baseline.
    from business_logic import calculate_expected_life
    return await calculate_expected_life(db, cam_id)


async def test_expected_life_zero_cycles(test_db, life_cam):
    assert await _expected(test_db, life_cam) == 0.375


async def test_expected_life_one_cycle(test_db, life_cam):
    await _cycles(test_db, life_cam, [0.3])
    assert await _expected(test_db, life_cam) == 0.35


async def test_expected_life_two_cycles(test_db, life_cam):
    await _cycles(test_db, life_cam, [0.15, 0.3])
    assert await _expected(test_db, life_cam) == 0.275


async def test_expected_life_three_cycles(test_db, life_cam):
    await _cycles(test_db, life_cam, [0.12, 0.21, 0.33])
    assert await _expected(test_db, life_cam) == 0.22


async def test_expected_life_last_three_cycles(test_db, life_cam):
    await _cycles(test_db, life_cam, [0.8, 0.9, 0.1, 0.2, 0.3])
    assert await _expected(test_db, life_cam) == 0.2


async def test_expected_life_skips_excluded_and_open_cycles(test_db, life_cam):
    await _cycles(test_db, life_cam, [0.12, 0.21, 0.33, 0.99, 0.8])
    await test_db.execute("UPDATE tool_lifespans SET exclude_from_avg = 1 WHERE lifespan_number = 4")
    await test_db.execute("UPDATE tool_lifespans SET ended_at = NULL WHERE lifespan_number = 5")
    assert await _expected(test_db, life_cam) == 0.22


async def test_expected_life_pads_excluded_cycles(test_db, life_cam):
    await _cycles(test_db, life_cam, [0.1, 0.99])
    await test_db.execute("UPDATE tool_lifespans SET exclude_from_avg = 1 WHERE lifespan_number = 2")
    assert await _expected(test_db, life_cam) == 0.2833


async def test_expected_life_custom_seed_and_rounding(test_db, life_cam):
    await test_db.execute("UPDATE cam_items SET max_material_life = 0.4 WHERE id = ?", (life_cam,))
    await _cycles(test_db, life_cam, [0.1234])
    assert await _expected(test_db, life_cam) == 0.3078


async def test_expected_life_configured_default(test_db, life_cam, monkeypatch):
    monkeypatch.setattr(config, 'DEFAULT_MATERIAL_LIFE', 0.48)
    await test_db.execute("UPDATE cam_items SET max_material_life = NULL WHERE id = ?", (life_cam,))
    assert await _expected(test_db, life_cam) == 0.48


async def test_open_new_lifespan_uses_rolling_value(test_db, life_cam):
    await _cycles(test_db, life_cam, [0.12, 0.21, 0.33])
    await open_new_lifespan(test_db, life_cam)
    row = await (await test_db.execute(
        "SELECT * FROM tool_lifespans WHERE cam_item_id = ? AND ended_at IS NULL", (life_cam,)
    )).fetchone()
    assert row['max_material_life'] == 0.22
    assert row['lifespan_number'] == 4
    assert row['exclude_from_avg'] == 0
    cam = await (await test_db.execute("SELECT max_material_life FROM cam_items WHERE id = ?", (life_cam,))).fetchone()
    assert cam['max_material_life'] == 0.375


async def test_forecast_skips_excluded_cycles(test_db, life_cam):
    await _cycles(test_db, life_cam, [0.12, 0.21, 0.33, 0.99])
    await test_db.execute("UPDATE tool_lifespans SET exclude_from_avg = 1 WHERE lifespan_number = 4")
    data = await get_lifespan_forecast(test_db, life_cam)
    assert data['history']['completed_lifespans'] == 3
    assert [r['lifespan_number'] for r in data['history']['last_3']] == [3, 2, 1]
    assert data['expected_life'] == 0.22


# Copied verbatim from f309103:database.py, not from the merged schema.
PHASE1_LIFESPANS_SQL = """
CREATE TABLE IF NOT EXISTS tool_lifespans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cam_item_id INTEGER NOT NULL,
    lifespan_number INTEGER NOT NULL DEFAULT 1,
    started_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    ended_at TIMESTAMP,
    total_material_removed REAL NOT NULL DEFAULT 0.0,
    sharpen_count INTEGER NOT NULL DEFAULT 0,
    max_material_life REAL NOT NULL DEFAULT 0.375,
    FOREIGN KEY (cam_item_id) REFERENCES cam_items(id) ON DELETE CASCADE,
    UNIQUE(cam_item_id, lifespan_number)
);
"""


def test_init_migrates_phase1_lifespans(tmp_path):
    path = str(tmp_path / 'phase1.db')
    with sqlite3.connect(path) as db:
        db.executescript(PHASE1_LIFESPANS_SQL)
        db.execute("INSERT INTO tool_lifespans (cam_item_id, lifespan_number) VALUES (1, 1)")
        db.execute("""INSERT INTO tool_lifespans (cam_item_id, lifespan_number, ended_at)
                      VALUES (1, 2, '2026-01-01 12:00:00')""")
    init_database(path)
    with sqlite3.connect(path) as db:
        assert 'exclude_from_avg' in {r[1] for r in db.execute('PRAGMA table_info(tool_lifespans)')}
        assert db.execute('SELECT exclude_from_avg FROM tool_lifespans ORDER BY id').fetchall() == [(1,), (0,)]
        # A new open cycle must stay included across later startup migrations.
        db.execute("INSERT INTO tool_lifespans (cam_item_id, lifespan_number) VALUES (1, 3)")
    init_database(path)
    migrate_database(path)
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT exclude_from_avg FROM tool_lifespans ORDER BY id').fetchall() == [(1,), (0,), (0,)]


def test_migrate_database_adds_exclusion_column(tmp_path):
    path = str(tmp_path / 'migration.db')
    with sqlite3.connect(path) as db:
        db.executescript(PHASE1_LIFESPANS_SQL)
        db.execute("INSERT INTO tool_lifespans (cam_item_id) VALUES (1)")
    migrate_database(path)
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT exclude_from_avg FROM tool_lifespans').fetchone() == (1,)


def test_fresh_schema_includes_exclusion_default(test_db_path):
    with sqlite3.connect(test_db_path) as db:
        columns = {r[1]: r for r in db.execute('PRAGMA table_info(tool_lifespans)')}
        assert columns['exclude_from_avg'][3:5] == (1, '0')


def test_backfilled_cycles_are_excluded(test_db_path):
    with sqlite3.connect(test_db_path) as db:
        db.execute("INSERT INTO jobs (s_number) VALUES ('9002')")
        db.execute("INSERT INTO cam_items (job_id, set_no, cam_no, status_station) VALUES (1, 1, 1, 'cabinet')")
        db.executemany(
            "INSERT INTO moves (cam_item_id, from_station, to_station) VALUES (1, ?, ?)",
            [('new', 'cabinet'), ('cabinet', 'refill'), ('refill', 'cabinet')]
        )
        db.execute('DROP TABLE tool_lifespans')
    migrate_database(test_db_path)
    with sqlite3.connect(test_db_path) as db:
        assert db.execute('SELECT exclude_from_avg FROM tool_lifespans ORDER BY lifespan_number').fetchall() == [(1,), (1,)]


@pytest.fixture
def api_cam(client, auth_headers):
    job = client.post('/api/jobs', json={'s_number': '9003'}, headers=auth_headers)
    assert job.status_code == 200, job.text
    cam = client.post('/api/cam-items', json={
        'job_id': job.json()['id'], 'set_no': 1, 'cam_no': 1, 'max_material_life': 0.375
    }, headers=auth_headers)
    assert cam.status_code == 200, cam.text
    return cam.json()['id']


def _refill(client, headers, cam_id, **fields):
    return client.post('/api/moves', json={'cam_item_id': cam_id, 'to_station': 'refill', **fields}, headers=headers)


def _prepare_exclusion_column(db):
    """Supply the flag on phase 1 too, so undo/CSV fail on behavior, not setup."""
    if 'exclude_from_avg' not in {r[1] for r in db.execute('PRAGMA table_info(tool_lifespans)')}:
        db.execute('ALTER TABLE tool_lifespans ADD COLUMN exclude_from_avg BOOLEAN NOT NULL DEFAULT 0')


@pytest.mark.parametrize('exclude', [True, False])
def test_early_refill_stores_note_and_decision(client, auth_headers, api_cam, test_db_path, exclude):
    current = client.get(f'/api/cam-items/{api_cam}/lifespan').json()['current_lifespan']
    assert current['early_refill_required'] is True
    note = 'Early refill: <edge> & inspection, "hold"'
    response = _refill(client, auth_headers, api_cam, notes=note, exclude_from_avg=exclude)
    assert response.status_code == 200, response.text
    with sqlite3.connect(test_db_path) as db:
        assert db.execute('SELECT notes FROM moves WHERE id = ?', (response.json()['move_id'],)).fetchone() == (note,)
        ended, flag = db.execute('SELECT ended_at, exclude_from_avg FROM tool_lifespans WHERE cam_item_id = ?', (api_cam,)).fetchone()
        assert ended is not None
        assert flag == int(exclude)


@pytest.mark.parametrize('removed,required', [(0.33749, True), (0.3375, False), (0.34, False)])
def test_refill_threshold_and_optional_api_fields(client, auth_headers, api_cam, test_db_path, removed, required):
    with sqlite3.connect(test_db_path) as db:
        db.execute('UPDATE tool_lifespans SET total_material_removed = ? WHERE cam_item_id = ?', (removed, api_cam))
    current = client.get(f'/api/cam-items/{api_cam}/lifespan').json()['current_lifespan']
    assert current['early_refill_required'] is required
    # The kiosk requires a reason above the threshold; the API stays optional.
    response = _refill(client, auth_headers, api_cam)
    assert response.status_code == 200, response.text
    with sqlite3.connect(test_db_path) as db:
        assert db.execute('SELECT exclude_from_avg FROM tool_lifespans WHERE cam_item_id = ?', (api_cam,)).fetchone() == (0,)
        assert db.execute('SELECT notes FROM moves WHERE id = ?', (response.json()['move_id'],)).fetchone() == (None,)


def test_undo_refill_resets_exclusion(client, auth_headers, api_cam, test_db_path):
    response = _refill(client, auth_headers, api_cam)
    assert response.status_code == 200, response.text
    with sqlite3.connect(test_db_path) as db:
        _prepare_exclusion_column(db)
        db.execute('UPDATE tool_lifespans SET exclude_from_avg = 1 WHERE cam_item_id = ?', (api_cam,))
    response = client.post(f'/api/moves/undo/{api_cam}', headers=auth_headers)
    assert response.status_code == 200, response.text
    with sqlite3.connect(test_db_path) as db:
        assert db.execute('SELECT ended_at, exclude_from_avg FROM tool_lifespans WHERE cam_item_id = ?', (api_cam,)).fetchone() == (None, 0)


def test_set_refill_keeps_per_cam_decisions(client, auth_headers, api_cam, test_db_path):
    with sqlite3.connect(test_db_path) as db:
        job_id = db.execute('SELECT job_id FROM cam_items WHERE id = ?', (api_cam,)).fetchone()[0]
    second = client.post('/api/cam-items', json={'job_id': job_id, 'set_no': 1, 'cam_no': 2}, headers=auth_headers).json()['id']
    response = client.post('/api/moves/set', json={
        'job_id': job_id, 'set_no': 1, 'to_station': 'refill',
        'notes': {str(api_cam): 'Early refill: inspection', str(second): 'Early refill: keep cycle'},
        'exclude_from_avg': {str(api_cam): True, str(second): False}
    }, headers=auth_headers)
    assert response.status_code == 200, response.text
    with sqlite3.connect(test_db_path) as db:
        assert db.execute('SELECT exclude_from_avg FROM tool_lifespans ORDER BY cam_item_id').fetchall() == [(1,), (0,)]
        assert db.execute("SELECT notes FROM moves WHERE to_station = 'refill' ORDER BY cam_item_id").fetchall() == [('Early refill: inspection',), ('Early refill: keep cycle',)]


def test_batch_refill_carries_decision(client, auth_headers, api_cam, test_db_path):
    response = client.post('/api/moves/batch', json={'moves': [{
        'cam_item_id': api_cam, 'to_station': 'refill',
        'notes': 'Early refill: inspection', 'exclude_from_avg': True
    }]}, headers=auth_headers)
    assert response.status_code == 200, response.text
    assert response.json()['moved'] == 1
    with sqlite3.connect(test_db_path) as db:
        assert db.execute('SELECT exclude_from_avg FROM tool_lifespans WHERE cam_item_id = ?', (api_cam,)).fetchone() == (1,)


def test_tool_lifespans_csv_new_columns(client, auth_headers, api_cam, test_db_path):
    with sqlite3.connect(test_db_path) as db:
        _prepare_exclusion_column(db)
        db.execute("""UPDATE tool_lifespans SET ended_at = '2026-01-01 12:00:00', exclude_from_avg = 1
                      WHERE cam_item_id = ?""", (api_cam,))
        db.executemany("""INSERT INTO moves (cam_item_id, from_station, to_station, notes, moved_at)
                          VALUES (?, 'cabinet', ?, ?, ?)""", [
            (api_cam, 'refill', 'Early refill: <edge>, "hold"', '2026-01-01 12:00:01'),
            (api_cam, 'refill', 'Early refill: too far away', '2026-01-01 12:00:03'),
            (api_cam, 'refill', 'Unrelated note', '2026-01-01 12:00:00'),
            (api_cam, 'sharpen', 'Early refill: wrong station', '2026-01-01 12:00:00'),
        ])
        db.execute('INSERT INTO tool_lifespans (cam_item_id, lifespan_number) VALUES (?, 2)', (api_cam,))
    response = client.get('/api/export/tool-lifespans/csv', headers=auth_headers)
    assert response.status_code == 200, response.text
    rows = list(csv.DictReader(io.StringIO(response.text)))
    assert len(rows) == 2
    assert rows[0]['exclude_from_avg'] == '1'
    assert rows[0]['early_refill_note'] == 'Early refill: <edge>, "hold"'
    assert rows[1]['exclude_from_avg'] == '0'
    assert rows[1]['early_refill_note'] == ''


def test_tool_summary_csv_removed(client, auth_headers):
    assert client.get('/api/export/tool-summary/csv', headers=auth_headers).status_code == 404
