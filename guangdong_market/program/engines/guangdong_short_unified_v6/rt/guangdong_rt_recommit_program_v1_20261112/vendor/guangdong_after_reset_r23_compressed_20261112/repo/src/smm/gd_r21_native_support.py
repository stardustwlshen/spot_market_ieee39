"""R21 fail-closed storage accounting; no native entrypoint or capabilities."""
from pathlib import Path
import json
import shutil
from .gd_r15_da_rt_bridge import canonical, digest, file_sha, require
from .gd_r16_native_support import source_manifest, audit_lp_certificate, reconstruct_solution_cells, _path_intact as _legacy_path_intact
EVIDENCE_CAP=2*1024**3
FILE_CAP=16*1024**2
WORKER_FILE_CAP=1024**2
DISK_FLOOR=1024**3
SHARED_CAP=64*1024**2
ESTIMATE_PATH=Path(__file__).resolve().parents[3]/'reconstruction/r21_day/pre_native_evidence_budget.json'
FIELDS=('input_bound_bytes','request_bound_bytes','names_bound_bytes','certificate_estimate_bytes','other_stage_allowance_bytes','worker_response_and_log_enforced_bytes')
ORDER=tuple(s+str(i) for i in range(96) for s in ('RT','PRICE'))
# Pinned after independent pure regeneration, not an accepted state or feasibility claim.
REVIEWED_SHAPE_SHA256='5f1a8a89608e1dc8b18b80d315e921d327019510629ca55d4fb44c14d4d95987'
REVIEWED_BUDGET_SHA256='615dcaf29fbab9840b33ade7a2acca31d57d9d4a712a0b69b5db2aa1b8aabbc0'

def shared_inventory():
    """Maximum live file counts and bytes; no cleanup or overwritten checkpoint assumed."""
    def item(count,per_file,total=None):return dict(max_files=count,per_file_cap_bytes=per_file,total_bound_bytes=count*per_file if total is None else total)
    d=dict(startup=item(6,4*1024**2,8*1024**2),frozen_quarters=item(96,32768),
        command_deltas=item(96,65536),receipt_deltas=item(97,32768),quarter_deltas=item(97,32768),
        hourly_checkpoints=item(24,131072),final_and_failed_reports=item(2,FILE_CAP),
        failure_snapshots=item(1,1024**2),compact_drain=item(1,65536))
    remaining=SHARED_CAP-sum(v['total_bound_bytes'] for v in d.values())
    d['other_shared']=item(64,remaining,remaining)
    return d

def shared_category(path):
    p=Path(path);name=p.name
    if len(p.parts)==1 and name in ('source_manifest.json','release.json','publication.json','pricing_policy.json','pre_native_evidence_budget.json','initial_session.json'):return 'startup'
    if name=='frozen_first_quarter.json':return 'frozen_quarters'
    if name=='issued_commands.json':return 'command_deltas'
    if name.startswith('receipts_'):return 'receipt_deltas'
    if name.startswith('quarter_deltas_'):return 'quarter_deltas'
    if name.startswith('hour') and name.endswith('_checkpoint.json'):return 'hourly_checkpoints'
    if name in ('result.json','failed_session_report.json'):return 'final_and_failed_reports'
    if name=='failure.json' or name.startswith('advance_failure_'):return 'failure_snapshots'
    if name=='drain_report.json':return 'compact_drain'
    return 'other_shared'

def _shared_usage(out,b,new_path=None,new_bytes=0):
    d=b['shared_category_allowances'];usage={k:[0,0] for k in d}
    records=[(p.relative_to(out),p.stat().st_size) for p in out.rglob('*') if p.is_file() and _stage(p.relative_to(out)) is None]
    if new_path is not None:records.append((Path(new_path),new_bytes))
    for path,size in records:
        k=shared_category(path);v=d[k]
        require(size<=v['per_file_cap_bytes'],'shared category file cap exceeded: '+k)
        usage[k][0]+=1;usage[k][1]+=size
    for k,(count,size) in usage.items():
        require(count<=d[k]['max_files'] and size<=d[k]['total_bound_bytes'],'shared category count/aggregate exceeded: '+k)
    return usage

def validate_evidence_budget(b):
    require(digest(b)==REVIEWED_BUDGET_SHA256,'reviewed serialized budget changed')
    require(b['schema']=='gd.research.r21.full_day_evidence_budget.v1' and b['optimizer_calls']==0 and set(b['stages'])==set(ORDER),'exact 192-stage pure budget required')
    require((b['evidence_cap_bytes'],b['per_parent_file_cap_bytes'],b['worker_file_cap_bytes'],b['minimum_free_bytes'])==(EVIDENCE_CAP,FILE_CAP,WORKER_FILE_CAP,DISK_FLOOR),'budget caps changed')
    require(b['shared_manifest_publication_reports_ledger_allowance_bytes']==SHARED_CAP,'shared allowance changed')
    shapes={k:[b['stages'][k]['columns'],b['stages'][k]['rows']] for k in ORDER}
    require(digest(shapes)==REVIEWED_SHAPE_SHA256,'reviewed dynamic all-day shapes changed')
    require(b['shared_category_allowances']==shared_inventory(),'reviewed shared inventory changed')
    require(sum(v['total_bound_bytes'] for v in b['shared_category_allowances'].values())==SHARED_CAP,'shared category sum')
    total=SHARED_CAP
    for v in b['stages'].values():
        require(all(type(v[k]) is int and 0<v[k]<=FILE_CAP for k in FIELDS),'invalid category bound')
        require(v['worker_response_and_log_enforced_bytes']==2*WORKER_FILE_CAP,'worker bounds changed')
        require(v['request_envelope_overhead_bound_bytes']==65536,'full request envelope overhead required')
        for key,field in [('input','input_bound_bytes'),('request_envelope','request_bound_bytes'),('names','names_bound_bytes')]:
            require(v[field]>=2*v['exact_serialized_template_bytes'][key]+65536,'template serialization margin missing')
        require(v['certificate_estimate_bytes']>=512*(v['columns']+v['rows'])+65536,'full row/column certificate allowance missing')
        require(v['other_stage_allowance_bytes']>=1024**2,'physical24cells and stage records allowance missing')
        require(sum(v['other_stage_category_allowances'].values())==v['other_stage_allowance_bytes'] and all(type(x) is int and x>=0 for x in v['other_stage_category_allowances'].values()),'auxiliary category accounting changed')
        require(v['other_stage_category_allowances']['acceptance_direct_cost_and_other']>=131072,'auxiliary remaining allowance missing')
        require(v['estimated_stage_bytes']==sum(v[k] for k in FIELDS),'stage sum mismatch')
        total+=v['estimated_stage_bytes']
    require(type(b['estimated_full_run_bytes']) is int and b['estimated_full_run_bytes']==total<=EVIDENCE_CAP,'whole-run budget mismatch/cap')
    require(b['external_core_and_preparation_bytes']<=b['external_source_allowance_bytes']==256*1024**2,'external resource allowance invalid')
    return True

def _budget(budget):
    b=json.loads(ESTIMATE_PATH.read_text()) if budget is None else budget
    validate_evidence_budget(b)
    return b

def _stage(path):
    q=Path(path).parts
    if len(q)>=3 and q[0].startswith('window') and q[0][6:].isdigit() and q[1].lower() in ('rt','price'):
        return q[1].upper()+str(int(q[0][6:]))
    return None

def _usage(out,b):
    require(not out.is_symlink(),'evidence root symlink forbidden')
    totals={k:dict.fromkeys(FIELDS,0) for k in ORDER};shared=0;used=0
    for p in out.rglob('*'):
        require(not p.is_symlink(),'evidence symlinks forbidden')
        if not p.is_file():continue
        n=p.stat().st_size;require(n<=FILE_CAP,'single file cap exceeded');used+=n
        key=_stage(p.relative_to(out))
        if key is None:shared+=n;continue
        require(key in totals,'unknown stage directory')
        category=_category(p.name)
        if p.name in ('response.json','worker.log'):require(n<=WORKER_FILE_CAP,'worker file cap exceeded')
        totals[key][category]+=n
    require(shared<=SHARED_CAP,'shared evidence allowance exceeded')
    _shared_usage(out,b)
    for k,v in totals.items():
        require(all(n<=b['stages'][k][f] for f,n in v.items()),'stage category allowance exceeded')
    return used,shared,totals

def _category(name):
    return {'input.json':'input_bound_bytes','request.json':'request_bound_bytes','names.json':'names_bound_bytes','full_certificate.json':'certificate_estimate_bytes','response.json':'worker_response_and_log_enforced_bytes','worker.log':'worker_response_and_log_enforced_bytes'}.get(name,'other_stage_allowance_bytes')

def _space(out,extra=0,budget=None):
    b=_budget(budget);out=Path(out);used,_,_=_usage(out,b)
    require(type(extra) is int and extra>=0,'nonnegative reserve required')
    remaining=max(extra,b['estimated_full_run_bytes']-used)
    free=shutil.disk_usage(out).free
    require(used+remaining<=EVIDENCE_CAP,'2 GiB evidence cap exceeded; preserve evidence')
    require(free-remaining>=DISK_FLOOR,'1 GiB free floor after worst remaining writes; preserve evidence')
    return dict(evidence_bytes=used,free_bytes=free,remaining_reserved_bytes=remaining,evidence_cap_bytes=EVIDENCE_CAP,minimum_free_bytes=DISK_FLOOR)

def _write(out,name,value,budget=None):
    b=_budget(budget);out=Path(out);p=Path(name)
    require(not p.is_absolute() and '..' not in p.parts,'relative evidence path required')
    raw=canonical(value);require(len(raw)<=FILE_CAP,'single evidence file exceeds 16 MiB')
    _,shared,totals=_usage(out,b);key=_stage(p)
    if key is None:
        require(shared+len(raw)<=SHARED_CAP,'shared allowance exceeded')
        _shared_usage(out,b,p,len(raw))
    else:
        require(key in totals,'unknown stage directory');cat=_category(p.name)
        require(totals[key][cat]+len(raw)<=b['stages'][key][cat],'serialized stage category exceeds reviewed bound')
        if p.name in ('response.json','worker.log'):require(len(raw)<=WORKER_FILE_CAP,'worker file cap')
    _space(out,len(raw),b);target=out/p;target.parent.mkdir(parents=True,exist_ok=True)
    with target.open('xb') as f:f.write(raw)
    return file_sha(target)

def _path_intact():
    return (_legacy_path_intact() and EVIDENCE_CAP==2*1024**3 and FILE_CAP==16*1024**2 and WORKER_FILE_CAP==1024**2 and DISK_FLOOR==1024**3 and all(globals()[k] is v for k,v in _ORIGINAL_FUNCTIONS.items()))
_ORIGINAL_FUNCTIONS={k:v for k,v in list(globals().items()) if callable(v)}
