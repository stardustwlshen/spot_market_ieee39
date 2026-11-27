from pathlib import Path
from math import fsum
import json
from functools import partial
from unittest.mock import patch
import sys
sys.path.insert(0, "app")
import sichuan_v5_dadu_run as run
from sichuan_v5_energy import realtime
key = sys.argv[1]
root = Path("runs_sichuan_v5_consultation")
base = run.read_dadu(root, "dadu19_20261127T095040Z_4639b392a04a")
assert base["inputs"]["scenario"]=="dry_peak" and base["company"]["strategy"]=="naive"
directory = Path("/tmp/dadu-sensitivity")/key
if key=="baseline":
    _, case = run.run_dadu(directory, scenario="dry_peak",strategy="naive",seed=39,periods=96)
elif key=="corridor":
    _, case = run.run_dadu(directory, scenario="dry_peak",strategy="naive",seed=39,periods=96,corridor_scale=1.2)
elif key=="horizon":
    with patch.object(run,"realtime",partial(realtime,window_quarters=16)):
        _, case = run.run_dadu(directory, scenario="dry_peak",strategy="naive",seed=39,periods=96)
else:
    raise ValueError(key)
def values(obj):
    losses=[fsum(q["shed"].values()) for q in obj["rt"]]
    return {"mwh":.25*fsum(losses),"periods":sum(v>1e-5 for v in losses),"maximum":max(losses),
            "gas_mwh":obj["summary"]["gas_mwh"],"cash":obj["summary"]["company_revenue"]}
print("DADU_SENSITIVITY",json.dumps({"setting":key,"baseline":values(base),"tested":values(case),
    "same_bid":base["summary"]["bid_hash"]==case["summary"]["bid_hash"],
    "same_da":all(all(abs(q["power"][uid]-p["power"][uid])<1e-4 for uid in q["power"]) for q,p in zip(base["da"],case["da"])),
    "same_issued":base["summary"]["issued_boundary_hash"]==case["summary"]["issued_boundary_hash"]}))
