from pathlib import Path
import json,gzip,tempfile,sys
from datetime import datetime,timedelta
A=Path(__file__).resolve().parents[1]/'app';sys.path.insert(0,str(A))
from gd_resume_runtime.same_period import derive,MODE
from content_hash import sha
r=Path(tempfile.mkdtemp());w=95;f=r/'pipeline/dispatch/W95'
def put(p,z):
 p.parent.mkdir(parents=True,exist_ok=True);b=json.dumps(z).encode();p.write_bytes(gzip.compress(b)if p.suffix=='.gz'else b)
def state(t):return dict(rt_clock_mode=MODE,origin='2026-01-01T00:00:00+08:00',boundary_at=(datetime.fromisoformat('2026-01-01T00:00:00+08:00')+timedelta(minutes=15*t)).isoformat(),delivery_tick=t,cutoff_tick=t,executed_end_exclusive=t,issued_end_exclusive=t,thermal={},thermal_age_h={},storage={})
put(r/'PLAN.json',dict(rt_clock_mode=MODE,single_day_scope='truncate_at_24h'));put(r/'pipeline/dispatch/W94/EXACT_CARRY.json',state(95));put(f/'prepared/CARRY.json',state(95));put(f/'EXACT_CARRY.json',state(96));put(f/'prepared/names.json',dict(variables=['dummy[0]']));put(f/'actual/SCED/response.json.gz',dict(primal=[0]));put(f/'actual/SCED/request.json.gz',{});put(f/'actual/SCED/COMPLETED.json',dict(response=dict(raw_sha256='raw95')))
put(f/'actual/ACCEPTED_PAIR.json',dict(status='accepted_research_pair',SCED_response_sha256=sha(f/'actual/SCED/response.json.gz')))
put(f/'RESULT.json',dict(window=95,executed_end_exclusive=96,issued_end_exclusive=96,branch_id='B',safety_release=False,handoff=dict(carry_sha256=sha(f/'EXACT_CARRY.json'),carry_path=str(f/'EXACT_CARRY.json'))))
put(r/'pipeline/price/q95/ACCEPTED.json',dict(context=dict(day_quarter=95,run_id='B',SCED_response_sha256='raw95'),KKT=dict(passed=True)))
for h in range(24):put(r/f'pipeline/price/HOUR{h}.json',{})
z=derive(r,95);assert z['executed_cells']==[95] and z['executed_boundary']['tick']==96 and z['interval']['end']=='2026-01-02T00:00:00+08:00';assert z['accounting']['pending_partial_hour_cells']==[] and z['accounting']['settled_end_exclusive']==96
print('terminal checkpoint: cell95 only, end24:00/tick96,24 settled hours,no pending energy PASS (synthetic, no solve)')
