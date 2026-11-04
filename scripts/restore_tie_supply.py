"""Preview or apply a backed-up supply restoration to one active scenario."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'))
from smm.tie_restore import prepare_supply_restore, apply_supply_restore

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scenario', required=True)
    parser.add_argument('--apply', action='store_true', help='Explicitly back up and restore this active scene')
    args = parser.parse_args()
    plan = prepare_supply_restore(args.scenario)
    print(json.dumps({key: value for key, value in plan.items() if key not in ('changes', 'fingerprint')}, ensure_ascii=False, indent=2))
    if args.apply:
        print(json.dumps(apply_supply_restore(plan), ensure_ascii=False, indent=2))
