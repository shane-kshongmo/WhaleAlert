#!/bin/bash
# Export SQLite tables to CSV files for Supabase import
# Usage: ./scripts/export_sqlite_to_csv.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
DB_PATH="$PROJECT_DIR/whale_alert.db"
EXPORT_DIR="$PROJECT_DIR/migration_csv"

echo "========================================="
echo "SQLite → CSV Export"
echo "========================================="
echo ""

cd "$PROJECT_DIR"

# Create export directory
mkdir -p "$EXPORT_DIR"
echo "📂 Export directory: $EXPORT_DIR"
echo ""

# Check database exists
if [ ! -f "$DB_PATH" ]; then
    echo "❌ Database not found: $DB_PATH"
    exit 1
fi

echo "📊 Exporting tables..."
echo ""

# Export snapshots
echo "  [1/5] Exporting snapshots..."
sqlite3 "$DB_PATH" << 'EOF'
.headers on
.mode csv
.output migration_csv/snapshots.csv
SELECT * FROM snapshots;
.quit
EOF
SNAPSHOTS=$(wc -l < migration_csv/snapshots.csv)
echo "        ✅ snapshots.csv ($((SNAPSHOTS - 1)) data rows)"

# Export paper_trades
echo "  [2/5] Exporting paper_trades..."
sqlite3 "$DB_PATH" << 'EOF'
.headers on
.mode csv
.output migration_csv/paper_trades.csv
SELECT * FROM paper_trades;
.quit
EOF
TRADES=$(wc -l < migration_csv/paper_trades.csv)
echo "        ✅ paper_trades.csv ($((TRADES - 1)) data rows)"

# Export ml_training_samples
echo "  [3/5] Exporting ml_training_samples..."
sqlite3 "$DB_PATH" << 'EOF'
.headers on
.mode csv
.output migration_csv/ml_training_samples.csv
SELECT * FROM ml_training_samples;
.quit
EOF
ML_SAMPLES=$(wc -l < migration_csv/ml_training_samples.csv)
echo "        ✅ ml_training_samples.csv ($((ML_SAMPLES - 1)) data rows)"

# Export pending_ml_samples
echo "  [4/5] Exporting pending_ml_samples..."
sqlite3 "$DB_PATH" << 'EOF'
.headers on
.mode csv
.output migration_csv/pending_ml_samples.csv
SELECT * FROM pending_ml_samples;
.quit
EOF
PENDING=$(wc -l < migration_csv/pending_ml_samples.csv)
echo "        ✅ pending_ml_samples.csv ($((PENDING - 1)) data rows)"

# Export pump_events
echo "  [5/5] Exporting pump_events..."
sqlite3 "$DB_PATH" << 'EOF'
.headers on
.mode csv
.output migration_csv/pump_events.csv
SELECT * FROM pump_events;
.quit
EOF
PUMPS=$(wc -l < migration_csv/pump_events.csv)
echo "        ✅ pump_events.csv ($((PUMPS - 1)) data rows)"

echo ""
echo "========================================="
echo "✅ Export complete!"
echo "========================================="
echo ""
echo "📁 CSV files created in: $EXPORT_DIR/"
echo ""
echo "Next steps:"
echo "  1. Go to Supabase Dashboard → Table Editor"
echo "  2. For each table, click 'Insert data' → 'Import from CSV'"
echo "  3. Upload the corresponding CSV file"
echo ""
echo "File sizes:"
ls -lh "$EXPORT_DIR"/*.csv
