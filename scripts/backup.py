"""Offline snapshot/restore. Keep snapshots private; stop backend before use."""
import argparse
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from app.services.backup import backup, restore

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=['backup', 'restore'])
    parser.add_argument('source', type=Path)
    parser.add_argument('destination', type=Path)
    parser.add_argument('--service-stopped', action='store_true', required=True,
                        help='Confirm that admissions are stopped, jobs drained, and the API is stopped')
    args = parser.parse_args()
    (backup if args.operation == 'backup' else restore)(args.source, args.destination)
    print('Snapshot verified' if args.operation == 'restore' else 'Snapshot created')
