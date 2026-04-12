#!/usr/bin/env python3
"""
Database Migration: Add Position Scaling Support (FIXED)

Populates entries_json for existing paper trades.
Run after schema migration if entries_json is NULL.
"""

import sqlite3
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

DB_PATH = PROJECT_ROOT / "whale_alert.db"

def migrate_existing_data():
    """Populate entries_json for existing trades"""

    print("=" * 70)
    print("Populating entries_json for existing trades...")
    print("=" * 70)
    print()

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # Get trades that need entries_json populated
    trades = cursor.execute('''
        SELECT id, symbol, entry_price, entry_time, position_size_usd, alert_score
        FROM paper_trades
        WHERE entries_json IS NULL
    ''').fetchall()

    if not trades:
        print("✅ All trades already have entries_json populated")
        return

    print(f"Found {len(trades)} trades to update...")
    print()

    for trade in trades:
        # Create initial entry JSON
        quantity_usd = trade['position_size_usd']
        if quantity_usd is None:
            quantity_usd = 300  # Default fallback

        # Calculate quantity (assuming position_size_usd = quantity * price)
        quantity = quantity_usd / trade['entry_price'] if trade['entry_price'] else 0

        entry_json = json.dumps([{
            'entry_time': trade['entry_time'],
            'price': trade['entry_price'],
            'quantity': quantity,
            'usd_value': quantity_usd,
            'score': trade['alert_score'] or 0,
            'reason': 'initial'
        }])

        cursor.execute('''
            UPDATE paper_trades
            SET entries_json = ?,
                initial_entry_price = entry_price,
                initial_entry_time = entry_time,
                entry_count = 1
            WHERE id = ?
        ''', (entry_json, trade['id']))

        print(f"  ✅ Trade #{trade['id']:2d}: {trade['symbol']:10s} initial entry populated")

    conn.commit()

    print()
    print("=" * 70)
    print("✅ Migration complete!")
    print("=" * 70)
    print()

if __name__ == '__main__':
    migrate_existing_data()
