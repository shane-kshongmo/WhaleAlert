#!/usr/bin/env python3
"""
Database Migration: Add Position Scaling Support

Adds columns to paper_trades table to track multiple entries per position.

Run this once to update the database schema:
    python3 scripts/migrate_add_position_scaling.py
"""

import sqlite3
import sys
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

DB_PATH = PROJECT_ROOT / "whale_alert.db"

def migrate():
    """Add position scaling columns to paper_trades table"""

    print("=" * 70)
    print("Database Migration: Position Scaling Support")
    print("=" * 70)
    print()

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Check if migration already ran
    cursor.execute("PRAGMA table_info(paper_trades)")
    columns = [col[1] for col in cursor.fetchall()]

    if 'entry_count' in columns:
        print("✅ Migration already applied (entry_count column exists)")
        print()
        print("Current schema includes:")
        if 'initial_entry_price' in columns:
            print("  ✅ initial_entry_price")
        if 'initial_entry_time' in columns:
            print("  ✅ initial_entry_time")
        if 'entry_count' in columns:
            print("  ✅ entry_count")
        if 'entries_json' in columns:
            print("  ✅ entries_json")
        return

    print("Adding new columns to paper_trades table...")
    print()

    # Add new columns
    new_columns = [
        ("initial_entry_price", "REAL"),
        ("initial_entry_time", "BIGINT"),
        ("entry_count", "INTEGER DEFAULT 1"),
        ("entries_json", "TEXT")
    ]

    for col_name, col_type in new_columns:
        try:
            cursor.execute(f"ALTER TABLE paper_trades ADD COLUMN {col_name} {col_type}")
            print(f"  ✅ Added column: {col_name} ({col_type})")
        except sqlite3.OperationalError as e:
            if "duplicate column name" in str(e):
                print(f"  ⚠️  Column already exists: {col_name}")
            else:
                print(f"  ❌ Error adding {col_name}: {e}")
                raise

    # Update existing rows to populate new columns
    print()
    print("Migrating existing data...")

    cursor.execute('''
        UPDATE paper_trades
        SET
            initial_entry_price = entry_price,
            initial_entry_time = entry_time,
            entry_count = 1,
            entries_json = json_array(
                json_object(
                    'entry_time', entry_time,
                    'price', entry_price,
                    'quantity', quantity,
                    'usd_value', quantity * entry_price,
                    'score', 0,
                    'reason', 'initial'
                )
            )
        WHERE entry_count IS NULL
    ''')

    updated = cursor.rowcount
    print(f"  ✅ Updated {updated} existing rows")

    # Commit changes
    conn.commit()

    print()
    print("=" * 70)
    print("✅ Migration complete!")
    print("=" * 70)
    print()
    print("New schema:")
    cursor.execute("PRAGMA table_info(paper_trades)")
    for col in cursor.fetchall():
        if col[1] in ['initial_entry_price', 'initial_entry_time', 'entry_count', 'entries_json']:
            print(f"  ✅ {col[1]:20s} {col[2]:15s}")

    print()
    print("Database is now ready for position scaling!")
    print()

if __name__ == '__main__':
    migrate()
