"""Thin gzip transport around explicitly pinned existing solver implementation."""
import sys,json,importlib.util,time
from pathlib import Path
from evidence_io import load,save,write_json,sha
job=json.loads(Path(sys.argv[1]).read_bytes());p=Path(job['worker'])
if sha(p.read_bytes())!=job['worker_sha256']:raise ValueError('worker source changed')
s=importlib.util.spec_from_file_location('_pinned_highs_worker',p);m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
calls=0
try:
 q=load(job['request']);calls=1;r=m.solve(q)
except Exception as exc:r={'error':type(exc).__name__+': '+str(exc)}
r['transport_solve_entries']=calls
r['optimize_calls']=m.OPTIMIZE_CALLS
ref=save(job['response_path'],r);write_json(sys.argv[2],{'response':ref,'solve_entries':calls,'worker_sha256':job['worker_sha256']})
