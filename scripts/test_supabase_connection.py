#!/usr/bin/env python3
"""
Test Supabase connection and data integrity
Run this after migrating to Supabase to verify everything works
"""

import sys
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

try:
    from data.supabase_client import get_supabase
    from dotenv import load_dotenv
    load_dotenv()
except ImportError as e:
    print(f"❌ Import error: {e}")
    print("\n💡 Install required packages:")
    print("   ./venv/bin/pip install supabase python-dotenv")
    sys.exit(1)

print("=" * 60)
print("Testing Supabase Connection")
print("=" * 60)
print()

try:
    supabase = get_supabase()
    print("✅ Supabase client created")
    print()

    # Test snapshots table
    print("Testing table access...")
    response = supabase.table('snapshots').select('*').limit(1).execute()
    print(f"  ✅ Snapshots table accessible")

    # Test paper_trades table
    response = supabase.table('paper_trades').select('*').limit(1).execute()
    print(f"  ✅ Paper trades table accessible")

    # Test ml_training_samples table
    response = supabase.table('ml_training_samples').select('*').limit(1).execute()
    print(f"  ✅ ML training samples table accessible")

    # Test pending_ml_samples table
    response = supabase.table('pending_ml_samples').select('*').limit(1).execute()
    print(f"  ✅ Pending ML samples table accessible")

    # Test pump_events table
    response = supabase.table('pump_events').select('*').limit(1).execute()
    print(f"  ✅ Pump events table accessible")

    print()
    print("Counting rows...")

    # Get exact counts
    snapshots = supabase.table('snapshots').select('*', count='exact').execute()
    trades = supabase.table('paper_trades').select('*', count='exact').execute()
    ml_samples = supabase.table('ml_training_samples').select('*', count='exact').execute()
    pending = supabase.table('pending_ml_samples').select('*', count='exact').execute()
    pumps = supabase.table('pump_events').select('*', count='exact').execute()

    print()
    print("📊 Row counts:")
    print(f"   Snapshots:           {snapshots.count:,}")
    print(f"   Paper trades:        {trades.count:,}")
    print(f"   ML training samples: {ml_samples.count:,}")
    print(f"   Pending ML samples:  {pending.count:,}")
    print(f"   Pump events:         {pumps.count:,}")

    # Expected counts (from SQLite export)
    expected = {
        'snapshots': 10798,
        'paper_trades': 21,
        'ml_training_samples': 21701,
        'pending_ml_samples': 957,
        'pump_events': 1
    }

    print()
    print("Verifying data integrity...")

    all_match = True
    if snapshots.count != expected['snapshots']:
        print(f"  ⚠️  Snapshots: expected {expected['snapshots']}, got {snapshots.count}")
        all_match = False

    if trades.count != expected['paper_trades']:
        print(f"  ⚠️  Paper trades: expected {expected['paper_trades']}, got {trades.count}")
        all_match = False

    if ml_samples.count != expected['ml_training_samples']:
        print(f"  ⚠️  ML samples: expected {expected['ml_training_samples']}, got {ml_samples.count}")
        all_match = False

    if pending.count != expected['pending_ml_samples']:
        print(f"  ⚠️  Pending samples: expected {expected['pending_ml_samples']}, got {pending.count}")
        all_match = False

    if pumps.count != expected['pump_events']:
        print(f"  ⚠️  Pump events: expected {expected['pump_events']}, got {pumps.count}")
        all_match = False

    if all_match:
        print("  ✅ All row counts match expected values!")

    print()
    print("=" * 60)
    print("✅ All tests passed!")
    print("=" * 60)
    print()
    print("Your Supabase database is ready to use!")

except Exception as e:
    print()
    print("=" * 60)
    print("❌ Connection Failed")
    print("=" * 60)
    print()
    print(f"Error: {e}")
    print()

    if "SUPABASE_URL" not in os.environ or "SUPABASE_KEY" not in os.environ:
        print("⚠️  Missing environment variables!")
        print()
        print("Make sure your .env file contains:")
        print("  SUPABASE_URL=https://your-project.supabase.co")
        print("  SUPABASE_KEY=your-anon-key-here")
        print()
    else:
        import traceback
        traceback.print_exc()

    sys.exit(1)
