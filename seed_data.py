#!/usr/bin/env python3
"""
Seed data script for CAM Tracking Kiosk
Creates test jobs and CAM items for development and testing
"""
import sqlite3
import sys
from datetime import datetime, timedelta
import random

import config
from database import init_database

def seed_database(db_path=None):
    """Populate database with test data"""
    if db_path is None:
        db_path = config.DATABASE_PATH

    # Initialize database first
    init_database(db_path)

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    print("Seeding database with test data...")

    # Create test jobs
    jobs = [
        ('1793', 'Front Panel Assembly', 'high'),
        ('2104', 'Rear Housing Unit', 'medium'),
        ('2387', 'Control Box Cover', 'low'),
        ('2501', 'Mounting Bracket', 'urgent'),
        ('2645', None, 'medium'),  # No title
    ]

    job_ids = {}

    for s_number, title, priority_level in jobs:
        try:
            cursor.execute(
                "INSERT INTO jobs (s_number, title, priority_level) VALUES (?, ?, ?)",
                (s_number, title, priority_level)
            )
            job_ids[s_number] = cursor.lastrowid
            print(f"  Created job S{s_number}")
        except sqlite3.IntegrityError:
            print(f"  Job S{s_number} already exists, skipping...")
            cursor.execute("SELECT id FROM jobs WHERE s_number = ?", (s_number,))
            job_ids[s_number] = cursor.fetchone()[0]

    conn.commit()

    # Create CAM items for each job
    stations = ['active', 'sharpen', 'cabinet', 'refill']

    # Job 1793: 3 sets, 4 cams each, mixed stations with die positions
    s_number = '1793'
    if s_number in job_ids:
        cam_configs = [
            # Set 1 (set_no, cam_no, die_position, enter_orientation, exit_orientation, station)
            (1, 1, 'upper', 'enter', 'exit', 'active'),
            (1, 2, 'upper', 'enter', 'exit', 'cabinet'),
            (1, 3, 'lower', 'enter', 'exit', 'cabinet'),
            (1, 4, 'lower', 'enter', 'exit', 'refill'),
            # Set 2
            (2, 1, 'upper', 'enter', 'exit', 'sharpen'),
            (2, 2, 'upper', 'enter', 'exit', 'cabinet'),
            (2, 3, 'lower', 'enter', 'exit', 'cabinet'),
            (2, 4, 'lower', 'enter', 'exit', 'cabinet'),
            # Set 3
            (3, 1, 'upper', 'enter', 'exit', 'cabinet'),
            (3, 2, 'upper', 'enter', 'exit', 'cabinet'),
            (3, 3, 'lower', 'enter', 'exit', 'sharpen'),
            (3, 4, 'lower', 'enter', 'exit', 'refill'),
        ]

        for set_no, cam_no, die_pos, enter_steel, exit_steel, station in cam_configs:
            try:
                cursor.execute(
                    """
                    INSERT INTO cam_items (job_id, set_no, cam_no, die_position, enter_die_steel, exit_die_steel, status_station)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (job_ids[s_number], set_no, cam_no, die_pos, enter_steel, exit_steel, station)
                )
                cam_item_id = cursor.lastrowid

                # Add initial move
                cursor.execute(
                    """
                    INSERT INTO moves (cam_item_id, from_station, to_station, operator, notes)
                    VALUES (?, 'new', ?, 'seed_script', 'Initial seed data')
                    """,
                    (cam_item_id, station)
                )
                print(f"    Created S{s_number} Set{set_no} Cam{cam_no} -> {station}")
            except sqlite3.IntegrityError:
                print(f"    S{s_number} Set{set_no} Cam{cam_no} already exists")

    # Job 2104: 2 sets, 6 cams each
    s_number = '2104'
    if s_number in job_ids:
        for set_no in [1, 2]:
            for cam_no in range(1, 7):
                # Mostly in cabinet, one active, one in sharpen
                if set_no == 1 and cam_no == 1:
                    station = 'active'
                elif set_no == 1 and cam_no == 2:
                    station = 'sharpen'
                elif set_no == 2 and cam_no == 6:
                    station = 'refill'
                else:
                    station = 'cabinet'

                try:
                    cursor.execute(
                        """
                        INSERT INTO cam_items (job_id, set_no, cam_no, status_station)
                        VALUES (?, ?, ?, ?)
                        """,
                        (job_ids[s_number], set_no, cam_no, station)
                    )
                    cam_item_id = cursor.lastrowid

                    cursor.execute(
                        """
                        INSERT INTO moves (cam_item_id, from_station, to_station, operator)
                        VALUES (?, 'new', ?, 'seed_script')
                        """,
                        (cam_item_id, station)
                    )
                    print(f"    Created S{s_number} Set{set_no} Cam{cam_no} -> {station}")
                except sqlite3.IntegrityError:
                    print(f"    S{s_number} Set{set_no} Cam{cam_no} already exists")

    # Job 2387: 1 set, 4 cams - all in refill (urgent scenario)
    s_number = '2387'
    if s_number in job_ids:
        for cam_no in range(1, 5):
            try:
                cursor.execute(
                    """
                    INSERT INTO cam_items (job_id, set_no, cam_no, status_station)
                    VALUES (?, ?, ?, ?)
                    """,
                    (job_ids[s_number], 1, cam_no, 'refill')
                )
                cam_item_id = cursor.lastrowid

                cursor.execute(
                    """
                    INSERT INTO moves (cam_item_id, from_station, to_station, operator)
                    VALUES (?, 'new', ?, 'seed_script')
                    """,
                    (cam_item_id, 'refill')
                )
                print(f"    Created S{s_number} Set1 Cam{cam_no} -> refill")
            except sqlite3.IntegrityError:
                print(f"    S{s_number} Set1 Cam{cam_no} already exists")

    # Job 2501: 4 sets, 3 cams each - simulate active usage
    s_number = '2501'
    if s_number in job_ids:
        for set_no in range(1, 5):
            for cam_no in range(1, 4):
                # Rotate through stations
                if set_no == 1:
                    station = 'active'
                elif set_no == 2:
                    station = 'sharpen'
                elif set_no == 3:
                    station = 'cabinet'
                else:
                    station = 'refill'

                try:
                    cursor.execute(
                        """
                        INSERT INTO cam_items (job_id, set_no, cam_no, status_station)
                        VALUES (?, ?, ?, ?)
                        """,
                        (job_ids[s_number], set_no, cam_no, station)
                    )
                    cam_item_id = cursor.lastrowid

                    cursor.execute(
                        """
                        INSERT INTO moves (cam_item_id, from_station, to_station, operator)
                        VALUES (?, 'new', ?, 'seed_script')
                        """,
                        (cam_item_id, station)
                    )

                    # Add some historical moves for this job to test analytics
                    if set_no == 1:
                        # Simulate some move history
                        historical_moves = [
                            ('cabinet', 'active', -5),
                            ('active', 'sharpen', -4),
                            ('sharpen', 'cabinet', -3),
                            ('cabinet', 'active', -1),
                        ]

                        for from_st, to_st, days_ago in historical_moves:
                            timestamp = datetime.now() + timedelta(days=days_ago)
                            cursor.execute(
                                """
                                INSERT INTO moves (cam_item_id, from_station, to_station, operator, moved_at)
                                VALUES (?, ?, ?, 'seed_script', ?)
                                """,
                                (cam_item_id, from_st, to_st, timestamp)
                            )

                    print(f"    Created S{s_number} Set{set_no} Cam{cam_no} -> {station}")
                except sqlite3.IntegrityError:
                    print(f"    S{s_number} Set{set_no} Cam{cam_no} already exists")

    # Job 2645: 2 sets, 5 cams each - all in sharpen (backlog scenario)
    s_number = '2645'
    if s_number in job_ids:
        for set_no in [1, 2]:
            for cam_no in range(1, 6):
                station = 'sharpen' if cam_no < 4 else 'cabinet'

                try:
                    cursor.execute(
                        """
                        INSERT INTO cam_items (job_id, set_no, cam_no, status_station)
                        VALUES (?, ?, ?, ?)
                        """,
                        (job_ids[s_number], set_no, cam_no, station)
                    )
                    cam_item_id = cursor.lastrowid

                    cursor.execute(
                        """
                        INSERT INTO moves (cam_item_id, from_station, to_station, operator)
                        VALUES (?, 'new', ?, 'seed_script')
                        """,
                        (cam_item_id, station)
                    )
                    print(f"    Created S{s_number} Set{set_no} Cam{cam_no} -> {station}")
                except sqlite3.IntegrityError:
                    print(f"    S{s_number} Set{set_no} Cam{cam_no} already exists")

    conn.commit()
    conn.close()

    print("\n✓ Database seeded successfully!")
    print(f"  Location: {db_path}")
    print("\nYou can now start the server with: python main.py")

if __name__ == "__main__":
    seed_database()
