"""Source-bound AGC award -> explicit research mileage estimate, not a bill."""
from pathlib import Path
from datetime import datetime, timedelta
from decimal import Decimal
import json, hashlib

def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def read(path): return json.loads(Path(path).read_text())
def num(x):
    if isinstance(x,bool): raise ValueError('boolean service value')
    n=Decimal(str(x))
    if not n.is_finite() or n<0: raise ValueError('invalid service value')
    return n

def collect(hour, execution, pins):
    periods={(r['start'],r['end']) for r in hour['rows']}
    if len(periods)!=1: raise ValueError('single hour required')
    start,end=next(iter(periods));found={}
    same_period=execution.get('rt_clock_mode')=='provincial_15min_execution'
    expected_starts={datetime.fromisoformat(start)+timedelta(minutes=15*i) for i in range(4)}
    current_bindings={}
    if same_period and datetime.fromisoformat(end)-datetime.fromisoformat(start)!=timedelta(hours=1):raise ValueError('exact current hour required')
    if execution['run_id']!=hour['run_id']:raise ValueError('execution run mismatch')
    if same_period:
        beginning=datetime.fromisoformat(start);ending=datetime.fromisoformat(end);origin=datetime.fromisoformat(execution['origin'])
        if any(t.tzinfo is None for t in (beginning,ending,origin)):raise ValueError('timezone-aware hour required')
        offset=(beginning-origin).total_seconds()/900
        if offset<0 or not offset.is_integer() or int(offset)%4:raise ValueError('hour not on execution grid')
        if execution['cells']!=list(range(int(offset),int(offset)+4)) or execution['hour']!=int(offset)//4:raise ValueError('executed cells mismatch current hour')
    for path,digest in execution.get('source_pins',{}).items():
        if Path(path).name!='AGC_BINDING.json':continue
        if sha(path)!=digest:raise ValueError('AGC binding changed')
        for binding in read(path):
            if same_period:
                stamp=datetime.fromisoformat(binding['domain_start'])
                if stamp not in expected_starts:continue
                if datetime.fromisoformat(binding['cutoff'])!=stamp:raise ValueError('current AGC binding clock mismatch')
                if stamp in current_bindings:raise ValueError('duplicate current AGC binding')
                current_bindings[stamp]=digest
            applicable=False
            for receipt_path,receipt_sha in binding.get('receipt_files',{}).items():
                original_receipt_path=receipt_path
                if not Path(receipt_path).is_file():
                    matches=[p for p,h in execution.get('source_pins',{}).items() if h==receipt_sha and Path(p).is_file() and sha(p)==receipt_sha]
                    if not matches:raise ValueError('AGC receipt absent; no hash-identical pinned recovery copy')
                    receipt_path=sorted(matches)[0]
                if sha(receipt_path)!=receipt_sha:raise ValueError('AGC receipt changed')
                receipt=read(receipt_path)
                if receipt.get('schema')!='gd.agc.hourly.research.v1':continue
                ctx=receipt['context']
                if (ctx.get('hour_start'),ctx.get('hour_end'))!=(start,end):continue
                if ctx['branch_id']!=binding['run_id']:raise ValueError('AGC branch mismatch')
                result=receipt['result']
                if result.get('price') is None or num(result.get('unmet_mw',0))!=0:continue
                if result.get('hour_start')!=start or result.get('binding_end')!=end:raise ValueError('AGC award time mismatch')
                value=dict(run_id=hour['run_id'],start=start,end=end,awards_mw=result['awards_mw'],
                           price=result['price'],performance={u['resource_id']:u['k'] for u in receipt['input']['units']},
                           source_path=receipt_path,source_sha256=receipt_sha,original_source_path=original_receipt_path,
                           source_binding_path=path,source_binding_sha256=digest)
                key=(start,end)
                if key in found and any(found[key][k]!=value[k] for k in ('awards_mw','price','performance')):raise ValueError('conflicting AGC receipts')
                found[key]=value;pins[path]=digest;pins[receipt_path]=receipt_sha;applicable=True
            if same_period and not applicable:raise ValueError('current quarter has no applicable AGC receipt')
    if same_period and set(current_bindings)!=expected_starts:raise ValueError('four current-quarter AGC bindings required')
    return list(found.values())

def estimate(view, periods, resources, mileage_per_capacity_hour=10):
    ratio=num(mileage_per_capacity_hour);by_period={}
    for r in view.get('agc_estimate_sources',[]):
        period=(r['start'],r['end'])
        if period not in periods:continue
        if r['run_id']!=view['run_id']:raise ValueError('AGC estimate run mismatch')
        for path_key,digest_key in [('source_path','source_sha256'),('source_binding_path','source_binding_sha256')]:
            if not r.get(path_key) or view.get('source_manifest',{}).get(r[path_key])!=r[digest_key]:raise ValueError('unbound AGC source')
        if period in by_period:raise ValueError('duplicate AGC period')
        by_period[period]=r
    if len(by_period)!=len(periods):return {}
    out={r:Decimal(0) for r in resources}
    for (start,end),s in by_period.items():
        hours=Decimal(str((datetime.fromisoformat(end)-datetime.fromisoformat(start)).total_seconds()))/3600
        if hours<=0:raise ValueError('invalid AGC interval')
        for r in resources:
            award=num(s['awards_mw'].get(r,0))
            if award:
                if r not in s['performance']:raise ValueError('missing awarded performance')
                out[r]+=award*hours*ratio*num(s['price'])*num(s['performance'][r])
    return {r:dict(amount_CNY=str(v),grade='research_mileage_estimate_gross',actual_mileage_missing=True,
                   mileage_per_capacity_hour=str(ratio),deductions_included=False,
                   zero_basis='not_awarded_in_selected_research_receipts' if v==0 else None)for r,v in out.items()}
