#!/usr/bin/env python3
"""
ML Training Samples Migration Script

Task: Backfill entry_price for 24,858 existing ml_training_samples

Approach:
1. For samples with matching kline_cache data: use cached close price
2. For older samples (before 2026-02-12): fetch from Binance historical klines API
3. Handle 5,040 circular label samples (label=1, actual_change=NULL)

Usage:
    python scripts/migrate_ml_samples_backfill_entry_price.py --dry-run
    python scripts/migrate_ml_samples_backfill_entry_price.py --execute
"""

import argparse
import logging
import sqlite3
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import httpx

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Binance API
BINANCE_KLINE_API = "https://data-api.binance.vision/api/v3/klines"

# Database
DB_PATH = Path("/mnt/d/finance/whale-alert-service/whale_alert.db")


class Migrator:
    def __init__(self, db_path: Path, dry_run: bool = True):
        self.db_path = db_path
        self.dry_run = dry_run
        self.conn = None
        self._stats = {
            "total_samples": 0,
            "from_kline_cache": 0,
            "from_binance_api": 0,
            "api_failures": 0,
            "circular_labels_deleted": 0,
            "circular_labels_relabel": 0,
        }

    def connect(self):
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row

    def close(self):
        if self.conn:
            self.conn.close()

    def get_summary(self) -> dict:
        """Get current state of ml_training_samples"""
        cursor = self.conn.cursor()

        cursor.execute("SELECT COUNT(*) FROM ml_training_samples")
        total = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM ml_training_samples WHERE entry_price IS NULL OR entry_price = 0")
        missing_price = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM ml_training_samples WHERE label = 1 AND actual_change IS NULL")
        circular = cursor.fetchone()[0]

        cursor.execute("SELECT MIN(timestamp), MAX(timestamp) FROM ml_training_samples")
        min_ts, max_ts = cursor.fetchone()

        return {
            "total_samples": total,
            "missing_entry_price": missing_price,
            "circular_labels": circular,
            "date_range": (datetime.fromtimestamp(min_ts/1000), datetime.fromtimestamp(max_ts/1000)),
        }

    def fetch_binance_kline(self, symbol: str, timestamp_ms: int) -> Optional[float]:
        """Fetch historical kline from Binance API"""
        try:
            # Convert to seconds for API
            timestamp_s = timestamp_ms // 1000

            params = {
                "symbol": symbol,
                "interval": "1h",
                "startTime": (timestamp_s - 3600) * 1000,  # 1 hour before
                "endTime": (timestamp_s + 3600) * 1000,    # 1 hour after
                "limit": 1
            }

            with httpx.Client(timeout=10.0) as client:
                response = client.get(BINANCE_KLINE_API, params=params)
                response.raise_for_status()
                data = response.json()

                if data and len(data) > 0:
                    # Return close price
                    return float(data[0][4])

        except Exception as e:
            logger.warning(f"Failed to fetch kline for {symbol} at {timestamp_ms}: {e}")
            self._stats["api_failures"] += 1

        return None

    def backfill_from_kline_cache(self) -> int:
        """Backfill entry_price using existing kline_cache data"""
        cursor = self.conn.cursor()

        # Find samples where we have kline data
        # Match by symbol and timestamp (within 1 hour)
        cursor.execute("""
            SELECT m.id, m.symbol, m.timestamp
            FROM ml_training_samples m
            WHERE m.entry_price IS NULL OR m.entry_price = 0
            AND EXISTS (
                SELECT 1 FROM kline_cache k
                WHERE k.symbol = m.symbol
                AND ABS(k.timestamp - m.timestamp) < 3600000
            )
            LIMIT 1000
        """)

        samples = cursor.fetchall()
        logger.info(f"Found {len(samples)} samples to backfill from kline_cache")

        updated = 0
        for sample in samples:
            sample_id, symbol, timestamp = sample["id"], sample["symbol"], sample["timestamp"]

            # Find closest kline
            cursor.execute("""
                SELECT close FROM kline_cache
                WHERE symbol = ? AND timestamp < ?
                ORDER BY ABS(timestamp - ?) ASC
                LIMIT 1
            """, (symbol, timestamp + 3600000, timestamp))

            row = cursor.fetchone()
            if row:
                entry_price = row["close"]

                if not self.dry_run:
                    cursor.execute("""
                        UPDATE ml_training_samples
                        SET entry_price = ?, label_verified = 0
                        WHERE id = ?
                    """, (entry_price, sample_id))

                updated += 1
                if updated % 100 == 0:
                    logger.info(f"Updated {updated}/{len(samples)} from kline_cache")

        self._stats["from_kline_cache"] = updated
        return updated

    def backfill_from_binance_api(self, batch_size: int = 100) -> int:
        """Backfill entry_price by fetching from Binance API"""
        cursor = self.conn.cursor()

        # Get samples without entry_price
        cursor.execute("""
            SELECT id, symbol, timestamp
            FROM ml_training_samples
            WHERE entry_price IS NULL OR entry_price = 0
            ORDER BY timestamp DESC
            LIMIT ?
        """, (batch_size,))

        samples = cursor.fetchall()
        logger.info(f"Fetching {len(samples)} entry prices from Binance API")

        updated = 0
        for i, sample in enumerate(samples):
            sample_id, symbol, timestamp = sample["id"], sample["symbol"], sample["timestamp"]

            # Fetch from Binance
            entry_price = self.fetch_binance_kline(symbol, timestamp)

            if entry_price:
                if not self.dry_run:
                    cursor.execute("""
                        UPDATE ml_training_samples
                        SET entry_price = ?, label_verified = 0
                        WHERE id = ?
                    """, (entry_price, sample_id))

                updated += 1
                logger.info(f"[{i+1}/{len(samples)}] {symbol} @ ${entry_price:.4f}")

            # Rate limiting
            if i < len(samples) - 1:
                time.sleep(0.1)

        self._stats["from_binance_api"] += updated
        self.conn.commit()
        return updated

    def handle_circular_labels(self, action: str = "delete") -> int:
        """
        Handle circular label samples (label=1, actual_change=NULL)

        action: "delete" or "relabel"
        """
        cursor = self.conn.cursor()

        cursor.execute("""
            SELECT COUNT(*) FROM ml_training_samples
            WHERE label = 1 AND actual_change IS NULL
        """)
        count = cursor.fetchone()[0]

        logger.info(f"Found {count} circular label samples (action: {action})")

        if action == "delete":
            if not self.dry_run:
                cursor.execute("""
                    DELETE FROM ml_training_samples
                    WHERE label = 1 AND actual_change IS NULL
                """)
                self.conn.commit()
            self._stats["circular_labels_deleted"] = count

        elif action == "relabel":
            # This would require fetching 24h-forward prices
            # For now, just mark them as label=0 (not a pump)
            if not self.dry_run:
                cursor.execute("""
                    UPDATE ml_training_samples
                    SET label = 0, actual_change = 0, label_verified = 0
                    WHERE label = 1 AND actual_change IS NULL
                """)
                self.conn.commit()
            self._stats["circular_labels_relabel"] = count

        return count

    def print_stats(self):
        logger.info("=" * 60)
        logger.info("MIGRATION STATISTICS")
        logger.info("=" * 60)
        logger.info(f"Total samples processed: {self._stats['total_samples']}")
        logger.info(f"Backfilled from kline_cache: {self._stats['from_kline_cache']}")
        logger.info(f"Backfilled from Binance API: {self._stats['from_binance_api']}")
        logger.info(f"API failures: {self._stats['api_failures']}")
        logger.info(f"Circular labels deleted: {self._stats['circular_labels_deleted']}")
        logger.info(f"Circular labels relabeled: {self._stats['circular_labels_relabel']}")

        # Show final state
        summary = self.get_summary()
        logger.info(f"\nFinal state:")
        logger.info(f"  Total samples: {summary['total_samples']:,}")
        logger.info(f"  Missing entry_price: {summary['missing_entry_price']:,}")
        logger.info(f"  Circular labels: {summary['circular_labels']:,}")


def main():
    parser = argparse.ArgumentParser(description="Migrate ML training samples")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be done without making changes")
    parser.add_argument("--execute", action="store_true", help="Execute the migration")
    parser.add_argument("--circular-action", choices=["delete", "relabel"], default="delete",
                       help="How to handle circular labels (default: delete)")
    parser.add_argument("--batch-size", type=int, default=100,
                       help="Batch size for Binance API calls (default: 100)")

    args = parser.parse_args()

    if not args.execute and not args.dry_run:
        parser.print_help()
        return

    migrator = Migrator(DB_PATH, dry_run=args.dry_run)
    migrator.connect()

    try:
        # Show initial state
        summary = migrator.get_summary()
        logger.info("=" * 60)
        logger.info("INITIAL STATE")
        logger.info("=" * 60)
        logger.info(f"Total samples: {summary['total_samples']:,}")
        logger.info(f"Missing entry_price: {summary['missing_entry_price']:,}")
        logger.info(f"Circular labels: {summary['circular_labels']:,}")
        logger.info(f"Date range: {summary['date_range'][0]} to {summary['date_range'][1]}")

        # Step 1: Backfill from kline_cache
        logger.info("\n" + "=" * 60)
        logger.info("STEP 1: Backfill from kline_cache")
        logger.info("=" * 60)
        migrator.backfill_from_kline_cache()

        # Step 2: Backfill from Binance API (repeat until done or limit)
        logger.info("\n" + "=" * 60)
        logger.info("STEP 2: Backfill from Binance API")
        logger.info("=" * 60)

        max_iterations = 250  # 250 * 100 = 25,000 samples max
        for i in range(max_iterations):
            updated = migrator.backfill_from_binance_api(args.batch_size)
            if updated == 0:
                logger.info("No more samples to process")
                break
            logger.info(f"Batch {i+1} complete: {updated} samples updated")

        # Step 3: Handle circular labels
        logger.info("\n" + "=" * 60)
        logger.info("STEP 3: Handle circular labels")
        logger.info("=" * 60)
        migrator.handle_circular_labels(args.circular_action)

        # Print final statistics
        migrator.print_stats()

    finally:
        migrator.close()


if __name__ == "__main__":
    main()
