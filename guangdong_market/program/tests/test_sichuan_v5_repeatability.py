"""Different interpreter hash seeds cannot reorder one unchanged market model."""
import json
import os
from pathlib import Path
import subprocess
import sys

PROGRAM=Path(__file__).resolve().parents[1]
SCRIPT=r'''
import hashlib, json, sys
from pathlib import Path
from market_rule_profile import SICHUAN_V5
from sichuan_v5_workflow import json_bytes,run

_, result = run(SICHUAN_V5.default_results(Path(sys.argv[1])),
                month=7, periods=4, coal_offer=220., equal_offer_priority=True)
print(json.dumps({key:hashlib.sha256(json_bytes(result[key])).hexdigest()
                  for key in ('da','rt','values')}))
'''


def test_unchanged_first_hour_repeats_across_process_hash_seeds(tmp_path):
    def result(seed):
        return json.loads(subprocess.check_output(
            [sys.executable,'-c',SCRIPT,str(tmp_path/str(seed))],cwd=PROGRAM,timeout=90,
            env={**os.environ,'PYTHONHASHSEED':str(seed),'PYTHONPATH':str(PROGRAM/'app')},
            text=True).splitlines()[-1])
    assert result(1)==result(2)
