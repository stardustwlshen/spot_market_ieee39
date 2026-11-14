"""New eight-call first-hour transaction: RT -> PRICE -> freeze -> issue, four times.

No old R13/RT-only permit, JSON response or caller callback grants acceptance.
Capabilities protect ordinary API use, not hostile introspective Python code.
"""
from copy import deepcopy
from datetime import datetime,timezone
import os
from pathlib import Path
import subprocess
import sys
import time
from uuid import uuid4
from weakref import WeakKeyDictionary
from . import gd_r16_hour_session as rt
from . import gd_r15_da_rt_bridge as bridge
from . import gd_r16_rt_pricing as pricing
from . import gd_r16_native_support as pure
from . import highs_solver as hs
from .gd_r16_native_identity import runtime_identity
from .gd_r15_da_rt_bridge import canonical,digest,file_sha,read_json,require,stamp,integrate_cell

SCHEMA='gd.research.r16.priced_first_hour.v1'
OPTIONS={stage:dict(threads=1,time_limit=limit,mip_rel_gap=.001,mip_abs_gap=0.,output_flag=False,presolve='choose',solve_relaxation=False) for stage,limit in [('RT',120.),('PRICE',60.)]}
ORDER=['RT0','PRICE0','RT1','PRICE1','RT2','PRICE2','RT3','PRICE3']
POLICY_SHA256=digest(pricing.POLICY)
WINDOW_POLICY_SHA256=digest(rt.WINDOW_POLICY)
EVIDENCE_CAP=pure.EVIDENCE_CAP;FILE_CAP=pure.FILE_CAP;DISK_FLOOR=pure.DISK_FLOOR
WORKER_FILE_CAP=1024**2
ESTIMATE_PATH=Path(__file__).resolve().parents[3]/'reconstruction/r16_hour/pre_native_evidence_budget.json'
_RUNS=WeakKeyDictionary();_RT=WeakKeyDictionary();_PRICE=WeakKeyDictionary()
source_manifest=pure.source_manifest;_space=pure._space
audit_lp_certificate=pure.audit_lp_certificate;reconstruct_solution_cells=pure.reconstruct_solution_cells

class PricedResearchRun:__slots__=('__weakref__',)
class AcceptedRTWindow:__slots__=('__weakref__',)
class AcceptedPriceWindow:__slots__=('__weakref__',)
def utc_now():return datetime.now(timezone.utc).isoformat()


def _path_intact():
    return (OPTIONS=={stage:dict(threads=1,time_limit=limit,mip_rel_gap=.001,mip_abs_gap=0.,output_flag=False,presolve='choose',solve_relaxation=False) for stage,limit in [('RT',120.),('PRICE',60.)]}
        and EVIDENCE_CAP==100*1024**2 and FILE_CAP==16*1024**2 and DISK_FLOOR==1024**3 and WORKER_FILE_CAP==1024**2
        and ORDER==['RT0','PRICE0','RT1','PRICE1','RT2','PRICE2','RT3','PRICE3'] and digest(pricing.POLICY)==POLICY_SHA256 and digest(rt.WINDOW_POLICY)==WINDOW_POLICY_SHA256 and pure._path_intact()
        and all(getattr(obj,name) is fn for obj,name,fn in _REFERENCES)
        and all(globals()[name] is fn for name,fn in _ORIGINALS.items()))


def validate_evidence_budget(estimate):
    """The reviewed envelope covers all eight stages and all shared writes."""
    require(estimate['schema']=='gd.research.r16.first_hour_evidence_budget.v1'
        and estimate['optimizer_calls']==0 and set(estimate['stages'])==set(ORDER), 'exact eight-stage zero-solve budget required')
    require(estimate['evidence_cap_bytes']==EVIDENCE_CAP and estimate['worker_file_cap_bytes']==WORKER_FILE_CAP
        and estimate['per_parent_file_cap_bytes']==FILE_CAP and estimate['minimum_free_bytes']==DISK_FLOOR, 'budget caps changed')
    shared=estimate['shared_manifest_publication_reports_ledger_allowance_bytes']
    require(type(shared) is int and shared==10*1024**2, 'exact reviewed shared allowance required')
    total=shared
    for key, b in estimate['stages'].items():
        shape=(1926,3658) if key.startswith('RT') else (966,1706)
        require((b['columns'],b['rows'])==shape, 'reviewed first-hour stage shape required')
        fields=('input_bound_bytes','request_bound_bytes','names_bound_bytes','certificate_estimate_bytes','other_stage_allowance_bytes')
        require(all(type(b[k]) is int and 0<b[k]<=FILE_CAP for k in fields), 'bounded positive stage allowance required')
        require(b['worker_response_and_log_enforced_bytes']==2*WORKER_FILE_CAP, 'worker budget mismatch')
        subtotal=sum(b[k] for k in fields)+2*WORKER_FILE_CAP
        require(b['estimated_stage_bytes']==subtotal, 'stage budget sum mismatch')
        total+=subtotal
    require(type(estimate['estimated_full_run_bytes']) is int and estimate['estimated_full_run_bytes']==total<EVIDENCE_CAP, 'whole-run budget does not fit 100 MiB')
    return True


def _write(out,name,value):
    """Enforce the serialized release budget, not only a whole-directory cap."""
    estimate=read_json(ESTIMATE_PATH);validate_evidence_budget(estimate)
    path=Path(name);require(not path.is_absolute() and '..' not in path.parts, 'relative evidence path required')
    raw=canonical(value);parts=path.parts
    stagekey=(parts[1].upper()+parts[0][6:]) if len(parts)>=3 and parts[0].startswith('window') and parts[1] in ('rt','price') else None
    if stagekey in estimate['stages']:
        b=estimate['stages'][stagekey];base=out/parts[0]/parts[1]
        categories={'input.json':'input_bound_bytes','request.json':'request_bound_bytes','names.json':'names_bound_bytes','full_certificate.json':'certificate_estimate_bytes'}
        if parts[-1] in categories:
            require(len(raw)<=b[categories[parts[-1]]], 'serialized evidence category exceeds reviewed bound')
        else:
            reserved=set(categories)|{'response.json','worker.log'}
            used=sum(f.stat().st_size for f in base.rglob('*') if f.is_file() and f.name not in reserved) if base.exists() else 0
            require(used+len(raw)<=b['other_stage_allowance_bytes'], 'other stage evidence exceeds reviewed allowance')
    else:
        def is_stage(f):
            q=f.relative_to(out).parts
            return len(q)>=3 and q[0] in ('window0','window1','window2','window3') and q[1] in ('rt','price')
        used=sum(f.stat().st_size for f in out.rglob('*') if f.is_file() and not is_stage(f))
        require(used+len(raw)<=estimate['shared_manifest_publication_reports_ledger_allowance_bytes'], 'shared evidence exceeds reviewed allowance')
    return pure._write(out,name,value)


def validate_release(path,expected_sha256):
    path=Path(path).resolve();require(file_sha(path)==expected_sha256,'release hash mismatch');r=read_json(path)
    require(r['schema']==SCHEMA+'.release' and r['status']=='approved_after_independent_review','independent priced release required')
    require(r['scope']=='exactly_RT0_PRICE0_RT1_PRICE1_RT2_PRICE2_RT3_PRICE3_no_DA_no_preparation_rerun' and r['stage_order']==ORDER and type(r['max_native_calls']) is int and r['max_native_calls']==8,'exact eight-call release required')
    require(r['solver_options']==OPTIONS and r['solver_version']=='1.15.1','released solver options mismatch')
    require(r['policy_sha256']==digest(pricing.POLICY) and r['window_policy_sha256']==digest(rt.WINDOW_POLICY),'reviewed pricing/window policy mismatch')
    require(r['worker_file_cap_bytes']==WORKER_FILE_CAP and r['serialized_evidence_estimate_sha256']==file_sha(ESTIMATE_PATH),'reviewed serialized evidence estimate mismatch')
    estimate=read_json(ESTIMATE_PATH);validate_evidence_budget(estimate)
    require(r['evidence_cap_bytes']==EVIDENCE_CAP and r['minimum_free_bytes']==DISK_FLOOR,'released storage bounds mismatch')
    review_path=Path(r['independent_review_path']);require(file_sha(review_path)==r['independent_review_sha256'],'review hash mismatch');review=read_json(review_path)
    require(review['status']=='pass' and review['native_execution_performed'] is False,'independent pre-native pass required')
    seal=source_manifest();require(r['source_tree_sha256']==review['source_tree_sha256']==seal['tree_sha256'],'reviewed source tree changed')
    require(r['runtime_identity']==runtime_identity() and _path_intact(),'runtime or execution path changed')
    return r,seal


def create_priced_run(*,r14_core,preparation_root,output,release_path,release_sha256):
    release,seal=validate_release(release_path,release_sha256);out=Path(output).resolve();source=Path(__file__).resolve().parents[3]
    require(not out.exists() and not out.is_relative_to(source),'new external evidence directory required')
    plan,permit=bridge.load_accepted_r14(r14_core,preparation_root=preparation_root)
    require(digest(plan['source_bindings'])==release['R14_bindings_sha256'] and digest(plan['preparation_source_bindings'])==release['preparation_bindings_sha256'],'released R14/preparation bindings mismatch')
    from .gd_r16_energy_ledger import new_ledger
    out.mkdir(parents=True,exist_ok=False);_space(out,EVIDENCE_CAP);run=PricedResearchRun()
    _RUNS[run]=dict(id='R16-PRICED-'+uuid4().hex,session=rt.initialize_hour(plan,permit),output=out,seal=seal,release=release,
        release_path=Path(release_path).resolve(),release_sha256=release_sha256,calls=0,verified_solver_calls=0,
        stopped=False,accepted=[],rt_caps=[],price_caps=[],receipts=[],executed_ids=set(),occurrences=[],
        ledger=new_ledger(),created_at_utc=utc_now())
    _write(out,'source_manifest.json',seal);_write(out,'release.json',release);_write(out,'publication.json',plan)
    _write(out,'pricing_policy.json',pricing.POLICY);_write(out,'pre_native_evidence_budget.json',read_json(ESTIMATE_PATH));_write(out,'initial_session.json',report(run));return run


def _run(run):
    require(type(run) is PricedResearchRun and run in _RUNS,'fresh priced run capability required')
    return _RUNS[run]


def _cap(cap,registry,kind):
    cls=AcceptedRTWindow if kind=='RT' else AcceptedPriceWindow
    require(type(cap) is cls and cap in registry,'registered fresh R16 '+kind+' capability required')
    c=registry[cap];e=_run(c['run'])
    require(not e['stopped'] and _path_intact(),'stopped or modified capability path')
    require(digest(c['data'])==c['seal'],'accepted source/report mutated')
    require(source_manifest()==e['seal'],'accepted source tree changed')
    for path,h in c['files'].items():require(file_sha(path)==h,'accepted native evidence mutated')
    return c


def accepted_rt_source(cap):return deepcopy(_cap(cap,_RT,'RT')['data'])
def accepted_price_report(cap):return deepcopy(_cap(cap,_PRICE,'PRICE')['data'])


def assert_same_run(run,rt_cap,price_cap):
    r=_cap(rt_cap,_RT,'RT');p=_cap(price_cap,_PRICE,'PRICE');e=_run(run)
    require(r['run'] is p['run'] is run and p['rt_cap'] is rt_cap,'same live RT/price run required')
    require(p['data']['source_binding_sha256']==digest(r['data']['binding']),'price/RT binding changed')
    return True


def chain_report(run):
    e=_run(run);s=rt._session(e['session'])
    return dict(logical_clock=s['clock'],commands=deepcopy(s['commands']),receipts=deepcopy(e['receipts']),accepted_RT_windows=len(e['accepted']),stopped=e['stopped'])


def _validate_occurrence(raw,envelope,request_path,names_path,pid):
    b=raw.get('occurrence',{});stage=envelope['stage'];opts=OPTIONS[stage]
    expected=dict(id=envelope['occurrence_id'],stage=stage,worker_pid=pid,request_sha256=file_sha(request_path),names_sha256=file_sha(names_path),source_tree_sha256=envelope['source_tree_sha256'],input_sha256=envelope['input_sha256'],policy_sha256=envelope['policy_sha256'],worker_file_sha256=envelope['worker_file_sha256'],native_run_count=1,runtime_identity=envelope['runtime_identity'])
    require(all(b.get(k)==v for k,v in expected.items()),'fresh typed process occurrence mismatch')
    require(raw.get('native_settings')=={k:opts[k] for k in ('output_flag','threads','presolve','mip_rel_gap','mip_abs_gap')},'applied native options mismatch')
    t=raw['time_settings'];require(t['wall_time_limit_s']==opts['time_limit'] and t['preparation_elapsed_s']==0. and 0<t['native_time_limit_s']<=opts['time_limit'] and 0<=t['transport_reserve_s'] and t['native_time_limit_s']+t['transport_reserve_s']<=opts['time_limit'],'native timing mismatch')
    a,z=map(datetime.fromisoformat,(b['started_at_utc'],b['finished_at_utc']))
    require(a.utcoffset().total_seconds()==z.utcoffset().total_seconds()==0 and datetime.fromisoformat(envelope['created_at_utc'])<=a<=z<=datetime.now(timezone.utc),'UTC occurrence chronology mismatch')


def _solve_stage(run,index,stage,inp,model,model_audit,diagnostic=None):
    e=_run(run);require(e['calls']<8 and ORDER[e['calls']]==stage+str(index),'exact causal stage order required')
    require(_path_intact() and source_manifest()==e['seal'],'stage source/path changed')
    out=e['output'];relative=f'window{index}/{stage.lower()}';directory=out/relative;directory.mkdir(parents=True,exist_ok=False)
    opts=OPTIONS[stage];payload,vs,cs=hs.serialize_model(model,opts);names=dict(variables=[v.name for v in vs],constraints=[c.name for c in cs])
    require(all(v['kind']=='C' for v in payload['variables']),'continuous LP only')
    budget=read_json(ESTIMATE_PATH)['stages'][stage+str(index)]
    require(len(payload['variables'])==budget['columns'] and len(payload['rows'])==budget['rows'],'reviewed stage shape changed')
    require(len(canonical(inp))<=budget['input_bound_bytes'] and len(canonical(payload))<=budget['request_bound_bytes'] and len(canonical(names))<=budget['names_bound_bytes'],'serialized stage exceeds reviewed estimate')
    _write(out,relative+'/input.json',inp);nh=_write(out,relative+'/names.json',names);_write(out,relative+'/model_audit.json',model_audit)
    if diagnostic is not None:_write(out,relative+'/selected_RT_diagnostic.json',diagnostic(inp,payload,names))
    started=time.perf_counter();payload.update(deadline=started+opts['time_limit'],preparation_elapsed_s=0.)
    worker=Path(__file__).with_name('gd_r16_priced_native_worker.py')
    envp=dict(schema=SCHEMA+'.request',stage=stage,occurrence_id='R16-'+stage+'-'+uuid4().hex,input_sha256=digest(inp),source_tree_sha256=e['seal']['tree_sha256'],names_sha256=nh,worker_file_sha256=file_sha(worker),policy_sha256=digest(pricing.POLICY),created_at_utc=utc_now(),runtime_identity=deepcopy(e['release']['runtime_identity']),payload=payload)
    request,response=directory/'request.json',directory/'response.json';_write(out,relative+'/request.json',envp)
    # Reserve the entire unspent reviewed whole-run envelope, not 64 MiB per stage.
    used=sum(f.stat().st_size for f in out.rglob('*') if f.is_file())
    _space(out,max(0,read_json(ESTIMATE_PATH)['estimated_full_run_bytes']-used))
    env=os.environ.copy();env.update(PYTHONDONTWRITEBYTECODE='1',OMP_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1',MKL_NUM_THREADS='1',TMPDIR=str(out))
    require(_path_intact(),'native process path replaced')
    with (directory/'worker.log').open('xb') as log:
        process=subprocess.Popen([sys.executable,'-B',str(worker),str(request),str(response),str(directory/'names.json')],env=env,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
        e['calls']+=1
        occurrence=dict(id=envp['occurrence_id'],stage=stage,index=index,worker_pid=process.pid,returncode=None,request_sha256=file_sha(request),names_sha256=nh,actual_launched_at_utc=utc_now(),outcome='launched_pending')
        e['occurrences'].append(occurrence)
        try:
            _write(out,relative+'/process_launch.json',occurrence)
            code=process.wait(timeout=max(.001,payload['deadline']-time.perf_counter()))
        except BaseException as exc:
            process.kill();code=process.wait()
            occurrence.update(returncode=code,actual_finished_at_utc=utc_now(),outcome='terminated_without_accepted_response',error=f'{type(exc).__name__}: {exc}')
            try:_write(out,relative+'/process_termination.json',occurrence)
            except Exception:pass
            raise
    occurrence.update(returncode=code,actual_finished_at_utc=utc_now(),outcome='returned_pending_verification')
    _write(out,relative+'/process_occurrence.json',occurrence)
    require(code==0 and response.is_file() and response.stat().st_size<=WORKER_FILE_CAP and (directory/'worker.log').stat().st_size<=WORKER_FILE_CAP,'native worker failed/absent/oversized response')
    raw=read_json(response);require(not raw.get('error'),'native worker error: '+str(raw.get('error')))
    _validate_occurrence(raw,envp,request,directory/'names.json',process.pid);e['verified_solver_calls']+=1
    occurrence['native_status']=raw.get('native_status');occurrence['outcome']='verified_native_response'
    _write(out,relative+'/process_verified.json',occurrence)
    require(_path_intact() and source_manifest()==e['seal'],'source/path changed during process')
    rebuilt,rv,rc=hs.serialize_model(model,opts)
    require(all(rebuilt[k]==payload[k] for k in ('variables','rows','objective','options')) and names==dict(variables=[v.name for v in rv],constraints=[c.name for c in rc]),'live price/RT model changed')
    certificate=audit_lp_certificate(payload,raw,names);_write(out,relative+'/full_certificate.json',certificate)
    files={str(p):file_sha(p) for p in directory.iterdir() if p.is_file()}
    return dict(directory=directory,relative=relative,names=names,raw=raw,certificate=certificate,occurrence=occurrence,files=files,payload=payload)


def run_priced_window(run,index):
    e=_run(run);require(not e['stopped'],'failed priced run permanently stopped')
    try:
        require(type(index) is int and index in (0,1,2,3) and index==len(e['accepted']) and e['calls']==2*index,'next complete priced window required')
        validate_release(e['release_path'],e['release_sha256']);s=rt._session(e['session'])
        inp,ticket=rt.prepare_window(e['session'],index);model,audit=rt.build_rt_model(inp,ticket)
        solved=_solve_stage(run,index,'RT',inp,model,audit);rt._ticket(inp,ticket)
        cells,terminal=reconstruct_solution_cells(inp,solved['names'],solved['raw']['primal'])
        out=e['output'];relative=solved['relative'];_write(out,relative+'/solution_cells.json',cells)
        binding=dict(schema=SCHEMA+'.accepted_RT',run_id=e['id'],transaction_id=inp['transaction_id'],occurrence=solved['occurrence'],window=deepcopy(inp['window']),
            input_sha256=digest(inp),solution_cells_sha256=digest(cells),request_sha256=file_sha(solved['directory']/'request.json'),response_sha256=file_sha(solved['directory']/'response.json'),names_sha256=file_sha(solved['directory']/'names.json'),certificate_sha256=file_sha(solved['directory']/'full_certificate.json'),
            source_tree_sha256=e['seal']['tree_sha256'],policy_sha256=digest(pricing.POLICY),runtime_identity=deepcopy(e['release']['runtime_identity']),objective_yuan=solved['certificate']['primal_objective'],full_horizon_cells=24,dispatch_prefix_cells=3,accepted_before_commit=True,official_market=False,execution_authority=False,actual_telemetry=False)
        _write(out,relative+'/acceptance.json',binding)
        data=dict(input=deepcopy(inp),cells=deepcopy(cells),binding=binding);cap=AcceptedRTWindow()
        files={str(p):file_sha(p) for p in solved['directory'].iterdir() if p.is_file()}
        _RT[cap]=dict(run=run,data=data,seal=digest(data),files=files);e['rt_caps'].append(cap)
        source=accepted_rt_source(cap);original,market=bridge.bridge_sources(s['plan'],s['permit'])
        pi=pricing.normalize_price_inputs(source['input'],source['cells'],s['plan'],market,original,source['binding'])
        pm,pa=pricing.build_price_model(pi)
        price=_solve_stage(run,index,'PRICE',pi,pm,pa,pricing.check_selected_reference_witness)
        accepted_rt_source(cap);rt._ticket(inp,ticket);bridge.validate_bridge(s['plan'],s['permit'],cutoff=inp['window']['cutoff'],recheck_files=True)
        direct=pricing.audit_independent_offer_cost(pi,price['names'],price['raw'])
        _write(out,price['relative']+'/direct_original_offer_cost.json',direct)
        result=pricing.extract_lmps(pi,price['names'],price['raw']);result['cost_comparison']=direct
        result.update(schema=SCHEMA+'.accepted_PRICE',run_id=e['id'],window=deepcopy(inp['window']),source_binding_sha256=digest(binding),
            model_sha256=digest({k:price['payload'][k] for k in ('variables','rows','objective','options')}),input_sha256=digest(pi),
            policy_sha256=digest(pricing.POLICY),occurrence=price['occurrence'],objective_yuan=price['certificate']['primal_objective'],
            price_available_at=inp['window']['cutoff'],actual_created_at_utc=utc_now(),first_quarter_freeze_status='separate_ledger_record_required',
            availability_is_synthetic=True,actual_operational_timeliness_claim=False,actual_telemetry=False,execution_authority=False,formal_bill=None)
        _write(out,price['relative']+'/acceptance.json',result);pcap=AcceptedPriceWindow()
        _PRICE[pcap]=dict(run=run,rt_cap=cap,data=result,seal=digest(result),files={str(p):file_sha(p) for p in price['directory'].iterdir() if p.is_file()});e['price_caps'].append(pcap)
        from .gd_r16_energy_ledger import freeze_first_quarter,ledger_report
        frozen=freeze_first_quarter(e['ledger'],pcap,cap,run)
        commands=[dict(id=s['id']+f'-W{index}-RT-{j}',kind='accepted_fresh_RT_research_command',issued_at=inp['window']['cutoff'],available_at=inp['window']['cutoff'],occurrence_id=binding['occurrence']['id'],acceptance_sha256=digest(binding),cell=deepcopy(c)) for j,c in enumerate(cells[:3])]
        _write(out,f'window{index}/frozen_first_quarter.json',frozen);_write(out,f'window{index}/issued_commands.json',commands)
        _write(out,f'window{index}/energy_ledger_checkpoint.json',ledger_report(e['ledger']))
        # First state commit occurs only after both complete witnesses and freeze persist.
        s['commands'].extend(commands);s['accepted_windows'].append(binding);s['next_window']+=1;s['prepared']=None;rt._TICKETS[ticket]['consumed']=True
        accepted=dict(window=deepcopy(inp['window']),RT=deepcopy(binding),PRICE=deepcopy(result),first_quarter_freeze_sha256=digest(frozen),issued_RT_cells=3)
        e['accepted'].append(accepted);return deepcopy(accepted)
    except BaseException as exc:
        e['stopped']=True
        failure=dict(schema=SCHEMA+'.failure',window=index,error=f'{type(exc).__name__}: {exc}',actual_created_at_utc=utc_now(),calls_attempted=e['calls'],verified_solver_calls=e['verified_solver_calls'],held_RT_windows=len(e['rt_caps']),accepted_price_windows=len(e['price_caps']),committed_windows=len(e['accepted']),retry_allowed=False,evidence_preserved=True,policy_repair_allowed=False)
        try:_write(e['output'],f'window{index}/failure.json',failure)
        except Exception:pass
        raise


def _advance_to_impl(run,target):
    e=_run(run);require(not e['stopped'],'failed run cannot advance');s=rt._session(e['session']);target=stamp(target)
    allowed={stamp(w['start']) for w in s['specs']}|{stamp(s['specs'][-1]['delivery_end'])}
    require(target in allowed and target>stamp(s['clock']),'declared forward boundary pause/drain required')
    require(e['accepted'], 'cannot advance before a committed RT/PRICE window')
    expected = stamp(s['specs'][len(e['accepted'])-1]['start'])
    if stamp(s['clock']) == expected and len(e['accepted']) == 4:
        expected = stamp(s['specs'][-1]['delivery_end'])
    require(target == expected, 'next committed start boundary or final W3 drain only')
    state=deepcopy(s['receipt']['state']);receipts=[]
    while stamp(state['at'])<target:
        matches=[c for c in s['commands'] if c['cell']['start']==state['at'] and stamp(c['issued_at'])<=stamp(c['available_at'])<=stamp(c['cell']['start'])]
        require(len(matches)==1,'missing/duplicate command at execution boundary');c=matches[0]
        require(c['id'] not in e['executed_ids'] and stamp(c['cell']['end'])<=target,'duplicate/future receipt')
        state=integrate_cell(state,c['cell'],s['plan']['technical'])
        receipts.append(dict(id=c['id']+'-SIMULATED-RECEIPT',at=state['at'],available_at=state['at'],state=deepcopy(state),command_id=c['id'],command_sha256=digest(c),is_measurement=False,kind='simulated_zero_tracking_execution_not_telemetry',RT=c['kind']=='accepted_fresh_RT_research_command'))
    _write(e['output'],f'receipts_{target.strftime("%H%M")}.json',dict(target=target.isoformat(),receipts=receipts))
    e['receipts'].extend(receipts);e['executed_ids'].update(r['command_id'] for r in receipts)
    if receipts:s['receipt']=deepcopy(receipts[-1])
    s['clock']=target.isoformat()
    from .gd_r16_energy_ledger import record_available_quarters,ledger_report
    record_available_quarters(e['ledger'],run);_write(e['output'],f'energy_ledger_{target.strftime("%H%M")}.json',ledger_report(e['ledger']))
    return report(run)


def advance_to(run,target):
    e=_run(run);require(not e['stopped'],'failed run cannot advance')
    try:return _advance_to_impl(run,target)
    except BaseException as exc:
        e['stopped']=True
        try:_write(e['output'],'advance_failure_'+uuid4().hex+'.json',dict(schema=SCHEMA+'.failure',target=str(target),error=f'{type(exc).__name__}: {exc}',actual_created_at_utc=utc_now(),retry_allowed=False,evidence_preserved=True))
        except Exception:pass
        raise


def report(run):
    e=_run(run);s=rt._session(e['session'])
    from .gd_r16_energy_ledger import ledger_report
    return dict(schema=SCHEMA+'.report',status='stopped' if e['stopped'] else 'research_session',actual_created_at_utc=e['created_at_utc'],logical_clock=s['clock'],
        held_audited_RT_windows=len(e['rt_caps']),accepted_RT_windows=len(e['accepted']),accepted_price_windows=len(e['price_caps']),issued_RT_cells=3*len(e['accepted']),executed_RT_cells=sum(r['RT'] for r in e['receipts']),
        preparation_commands=3,executed_preparation_cells=sum(not r['RT'] for r in e['receipts']),native_process_calls=e['calls'],verified_native_solver_calls=e['verified_solver_calls'],
        stage_occurrences=deepcopy(e['occurrences']),new_preparation_calls=0,new_DA_calls=0,independent_RT_price_calls=sum(o['stage']=='PRICE' for o in e['occurrences']),
        formal_bill=None,official_price=None,actual_telemetry=False,official_market=False,execution_authority=False,price_dispatch_executable=False,
        native_solver_call_count_uncertain=e['calls']!=e['verified_solver_calls'],accepted=deepcopy(e['accepted']),ledger=ledger_report(e['ledger']),storage=_space(e['output']))


from . import gd_r16_energy_ledger as energy_ledger
_REFERENCES=[(subprocess,'Popen',subprocess.Popen),(subprocess.Popen,'wait',subprocess.Popen.wait),(hs,'serialize_model',hs.serialize_model),
    (rt,'build_rt_model',rt.build_rt_model),(rt,'prepare_window',rt.prepare_window),
    (pricing,'build_price_model',pricing.build_price_model),(pricing,'normalize_price_inputs',pricing.normalize_price_inputs),
    (pricing,'audit_independent_offer_cost',pricing.audit_independent_offer_cost),(pricing,'independent_offer_cost',pricing.independent_offer_cost),(pricing,'validate_price_inputs',pricing.validate_price_inputs),(pricing,'derive_quarters',pricing.derive_quarters),(pricing,'_curve',pricing._curve),
    (energy_ledger,'freeze_first_quarter',energy_ledger.freeze_first_quarter),(energy_ledger,'record_available_quarters',energy_ledger.record_available_quarters),(energy_ledger,'validate_quarter_receipts',energy_ledger.validate_quarter_receipts),
    (pricing,'extract_lmps',pricing.extract_lmps),(pricing,'check_selected_reference_witness',pricing.check_selected_reference_witness)]
_REFERENCES += [(energy_ledger,name,getattr(energy_ledger,name)) for name in ('quarter_energy_rows','_hourly_arithmetic')]
_REFERENCES += [(module,name,fn) for module in (rt,pricing,energy_ledger,bridge,pure) for name,fn in vars(module).items() if callable(fn)]
_ORIGINALS={name:fn for name,fn in list(globals().items()) if callable(fn)}
_ORIGINALS.update({name:globals()[name] for name in ('source_manifest','_write','_space','audit_lp_certificate','reconstruct_solution_cells','runtime_identity')})
