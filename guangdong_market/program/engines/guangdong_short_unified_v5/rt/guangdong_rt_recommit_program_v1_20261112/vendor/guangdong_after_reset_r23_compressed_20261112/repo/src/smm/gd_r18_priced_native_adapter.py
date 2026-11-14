"""New 192-call whole-day transaction: RT -> PRICE -> freeze -> issue,96 times.

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
from . import gd_r18_day_session as rt
from . import gd_r15_da_rt_bridge as bridge
from . import gd_r17_midnight_contract as contract
from . import gd_r18_rt_pricing as pricing
from . import gd_r18_native_support as pure
from . import highs_solver as hs
from .gd_r18_native_identity import runtime_identity
from .gd_r15_da_rt_bridge import canonical,digest,file_sha,read_json,require,stamp,integrate_cell

SCHEMA='gd.research.r18.priced_full_day.v1'
OPTIONS={stage:dict(threads=1,time_limit=limit,mip_rel_gap=.001,mip_abs_gap=0.,output_flag=False,presolve='choose',solve_relaxation=False) for stage,limit in [('RT',120.),('PRICE',60.)]}
ORDER=[stage+str(i) for i in range(96) for stage in ('RT','PRICE')]
POLICY_SHA256=digest(pricing.POLICY)
WINDOW_POLICY_SHA256=digest(rt.WINDOW_POLICY)
CONTRACT_POLICY_SHA256=digest(contract.POLICY)
EVIDENCE_CAP=pure.EVIDENCE_CAP;FILE_CAP=pure.FILE_CAP;DISK_FLOOR=pure.DISK_FLOOR
WORKER_FILE_CAP=1024**2
ESTIMATE_PATH=Path(__file__).resolve().parents[3]/'reconstruction/r18_day/pre_native_evidence_budget.json'
_RUNS=WeakKeyDictionary();_RT=WeakKeyDictionary();_PRICE=WeakKeyDictionary()
_NATIVE_ENTRY_KEY=object()
source_manifest=pure.source_manifest;_space=pure._space
audit_lp_certificate=pure.audit_lp_certificate;reconstruct_solution_cells=pure.reconstruct_solution_cells

class PricedResearchRun:__slots__=('__weakref__',)
class AcceptedRTWindow:__slots__=('__weakref__',)
class AcceptedPriceWindow:__slots__=('__weakref__',)
def utc_now():return datetime.now(timezone.utc).isoformat()


def _path_intact():
    return (OPTIONS=={stage:dict(threads=1,time_limit=limit,mip_rel_gap=.001,mip_abs_gap=0.,output_flag=False,presolve='choose',solve_relaxation=False) for stage,limit in [('RT',120.),('PRICE',60.)]}
        and EVIDENCE_CAP==2*1024**3 and FILE_CAP==16*1024**2 and DISK_FLOOR==1024**3 and WORKER_FILE_CAP==1024**2
        and ORDER==[stage+str(i) for i in range(96) for stage in ('RT','PRICE')] and digest(pricing.POLICY)==POLICY_SHA256 and digest(rt.WINDOW_POLICY)==WINDOW_POLICY_SHA256 and digest(contract.POLICY)==CONTRACT_POLICY_SHA256 and pure._path_intact()
        and all(getattr(obj,name) is fn for obj,name,fn in _REFERENCES)
        and all(globals()[name] is fn for name,fn in _ORIGINALS.items()))


validate_evidence_budget=pure.validate_evidence_budget
_write=pure._write


def validate_release(path,expected_sha256):
    path=Path(path).resolve();require(file_sha(path)==expected_sha256,'release hash mismatch');r=read_json(path)
    require(r['schema']==SCHEMA+'.release' and r['status']=='approved_after_independent_review','independent priced release required')
    require(r['scope']=='exactly_96_RT_PRICE_pairs_192_fresh_LPs_no_DA_no_preparation_rerun' and r['stage_order']==ORDER and type(r['max_native_calls']) is int and r['max_native_calls']==192,'exact 192-call release required')
    require(r['solver_options']==OPTIONS and r['solver_version']=='1.15.1','released solver options mismatch')
    require(r['contract_policy_sha256']==digest(contract.POLICY),'reviewed source contract policy mismatch')
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
    plan,permit=contract.load_contract(r14_core,preparation_root=preparation_root,policy=contract.POLICY)
    require(release['contract_sha256']==digest(plan) and release['contract_policy_sha256']==digest(contract.POLICY),'released full source contract/policy mismatch')
    require(digest(plan['source_bindings'])==release['R14_bindings_sha256'] and digest(plan['preparation_source_bindings'])==release['preparation_bindings_sha256'],'released R14/preparation bindings mismatch')
    from .gd_r18_energy_ledger import new_ledger
    out.mkdir(parents=True,exist_ok=False);_space(out);run=PricedResearchRun()
    _RUNS[run]=dict(id='R18-PRICED-'+uuid4().hex,session=rt.initialize_day(plan,permit),output=out,seal=seal,release=release,
        release_path=Path(release_path).resolve(),release_sha256=release_sha256,calls=0,verified_solver_calls=0,launch_attempts=0,failure=None,phase='INITIALIZED',
        stopped=False,accepted=[],rt_caps=[],price_caps=[],receipts=[],executed_ids=set(),occurrences=[],
        ledger=new_ledger(),created_at_utc=utc_now())
    rt._SESSIONS[_RUNS[run]['session']]['run']=run
    _write(out,'source_manifest.json',seal);_write(out,'release.json',release);_write(out,'publication.json',plan)
    _write(out,'pricing_policy.json',pricing.POLICY);_write(out,'pre_native_evidence_budget.json',read_json(ESTIMATE_PATH));_write(out,'initial_session.json',report(run));return run


def _run(run):
    require(type(run) is PricedResearchRun and run in _RUNS,'fresh priced run capability required')
    return _RUNS[run]


def _cap_in_verified_run(cap,registry,kind,run=None):
    """Check capability and all native files; caller separately seals source."""
    cls=AcceptedRTWindow if kind=='RT' else AcceptedPriceWindow
    require(type(cap) is cls and cap in registry,'registered fresh R18 '+kind+' capability required')
    c=registry[cap];e=_run(c['run'])
    require(run is None or c['run'] is run,'capability belongs to another run')
    require(not e['stopped'],'stopped capability path')
    require(digest(c['data'])==c['seal'],'accepted source/report mutated')
    for path,h in c['files'].items():require(file_sha(path)==h,'accepted native evidence mutated')
    return c


def _cap(cap,registry,kind):
    c=_cap_in_verified_run(cap,registry,kind);e=_run(c['run'])
    require(_path_intact() and source_manifest()==e['seal'],'accepted source/path changed')
    return c


def accepted_rt_source(cap):return deepcopy(_cap(cap,_RT,'RT')['data'])
def accepted_price_report(cap):return deepcopy(_cap(cap,_PRICE,'PRICE')['data'])


def assert_rt_run(run,rt_cap):
    _run(run);r=_cap(rt_cap,_RT,'RT')
    require(r['run'] is run,'same live R18 RT run required')
    return True


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
    expected=dict(id=envelope['occurrence_id'],stage=stage,worker_pid=pid,request_sha256=file_sha(request_path),names_sha256=file_sha(names_path),source_tree_sha256=envelope['source_tree_sha256'],input_sha256=envelope['input_sha256'],policy_sha256=envelope['policy_sha256'],worker_file_sha256=envelope['worker_file_sha256'],native_run_count=1,runtime_identity=envelope['runtime_identity'],window_index=envelope['window_index'],stage_ordinal=envelope['stage_ordinal'],run_id=envelope['run_id'],contract_sha256=envelope['contract_sha256'],window_policy_sha256=envelope['window_policy_sha256'])
    require(all(b.get(k)==v for k,v in expected.items()),'fresh typed process occurrence mismatch')
    require(raw.get('native_settings')=={k:opts[k] for k in ('output_flag','threads','presolve','mip_rel_gap','mip_abs_gap')},'applied native options mismatch')
    t=raw['time_settings'];require(t['wall_time_limit_s']==opts['time_limit'] and t['preparation_elapsed_s']==0. and 0<t['native_time_limit_s']<=opts['time_limit'] and 0<=t['transport_reserve_s'] and t['native_time_limit_s']+t['transport_reserve_s']<=opts['time_limit'],'native timing mismatch')
    a,z=map(datetime.fromisoformat,(b['started_at_utc'],b['finished_at_utc']))
    require(a.utcoffset().total_seconds()==z.utcoffset().total_seconds()==0 and datetime.fromisoformat(envelope['created_at_utc'])<=a<=z<=datetime.now(timezone.utc),'UTC occurrence chronology mismatch')



def _wait_owned_worker(process,deadline,occurrence,out,relative):
    """Bounded wait/termination only. This helper cannot launch any process."""
    try:
        _write(out,relative+'/process_launch.json',occurrence)
        return process.wait(timeout=max(.001,deadline-time.perf_counter()))
    except BaseException as exc:
        occurrence.update(outcome='termination_requested_native_outcome_unknown',error=f'{type(exc).__name__}: {exc}')
        try:
            process.kill();code=process.wait(timeout=5.)
            occurrence.update(returncode=code,actual_finished_at_utc=utc_now(),outcome='terminated_without_accepted_response')
        except BaseException as cleanup:
            occurrence['termination_error']=f'{type(cleanup).__name__}: {cleanup}'
        try:_write(out,relative+'/process_termination.json',occurrence)
        except Exception:pass
        raise


def _solve_stage_impl(run,index,stage,inp,model,model_audit,diagnostic=None,*,ticket,_entry):
    require(_entry is _NATIVE_ENTRY_KEY,'native boundary must use permanent-stop wrapper')
    e=_run(run);require(not e['stopped'],'failed run cannot cross native boundary')
    _validate_stage_ticket(run,index,stage,inp,model,ticket)
    require(e['calls']<192 and ORDER[e['calls']]==stage+str(index),'exact causal stage order required')
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
    worker=Path(__file__).with_name('gd_r18_priced_native_worker.py')
    envp=dict(schema=SCHEMA+'.request',stage=stage,window_index=index,stage_ordinal=e['calls'],run_id=e['id'],contract_sha256=e['release']['contract_sha256'],window_policy_sha256=digest(rt.WINDOW_POLICY),occurrence_id='R18-'+stage+'-'+uuid4().hex,input_sha256=digest(inp),source_tree_sha256=e['seal']['tree_sha256'],names_sha256=nh,worker_file_sha256=file_sha(worker),policy_sha256=digest(pricing.POLICY),created_at_utc=utc_now(),runtime_identity=deepcopy(e['release']['runtime_identity']),payload=payload)
    request,response=directory/'request.json',directory/'response.json';_write(out,relative+'/request.json',envp)
    # Reserve the entire unspent reviewed whole-run envelope, not 64 MiB per stage.
    used=sum(f.stat().st_size for f in out.rglob('*') if f.is_file())
    _space(out,max(0,read_json(ESTIMATE_PATH)['estimated_full_run_bytes']-used))
    env=os.environ.copy();env.update(PYTHONDONTWRITEBYTECODE='1',OMP_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1',MKL_NUM_THREADS='1',TMPDIR=str(out))
    require(_path_intact(),'native process path replaced')
    with (directory/'worker.log').open('xb') as log:
        occurrence=dict(id=envp['occurrence_id'],stage=stage,index=index,worker_pid=None,returncode=None,request_sha256=file_sha(request),names_sha256=nh,actual_attempted_at_utc=utc_now(),outcome='prepared_not_launched')
        e['launch_attempts']=e.get('launch_attempts',0)+1;e['occurrences'].append(occurrence)
        _write(out,relative+'/process_attempt.json',occurrence)
        occurrence['outcome']='launch_attempt_started_process_outcome_unknown'
        process=subprocess.Popen([sys.executable,'-B',str(worker),str(request),str(response),str(directory/'names.json')],env=env,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
        e['calls']+=1
        occurrence.update(worker_pid=process.pid,actual_launched_at_utc=utc_now(),outcome='launched_pending')
        code=_wait_owned_worker(process,payload['deadline'],occurrence,out,relative)
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



def _validate_stage_ticket(run,index,stage,inp,model,ticket):
    e=_run(run);require(not e['stopped'],'failed run cannot cross native boundary')
    if stage=='RT':
        t=rt._ticket(inp,ticket);s=rt._session(t['session'])
        require(e['session'] is t['session'] and s['run'] is run,'native RT ticket belongs to another run')
        w=inp['window']
    elif stage=='PRICE':
        t=pricing._price_ticket(inp,ticket);require(t['run'] is run,'native PRICE ticket belongs to another run');w=inp['rt_input']['window']
    else:raise ValueError('native stage type invalid')
    require(type(index) is int and index==w['index'] and t['model'] is model,'exact live ticket window/model required')
    require(t['model_sha256']==rt.model_fingerprint(model,stage),'model mutated after live construction')
    return True


def _native_uncertain(e):
    return e['calls']!=e['verified_solver_calls'] or any(o['outcome'] in ('launch_attempt_started_process_outcome_unknown','launched_pending','returned_pending_verification','terminated_without_accepted_response','termination_requested_native_outcome_unknown') for o in e.get('occurrences',[]))


def failure_snapshot(run,*,error=None,window=None,stage=None,target=None):
    e=_run(run);occurrences=e.get('occurrences',[])
    uncertain=_native_uncertain(e)
    session=rt._SESSIONS.get(e.get('session')) if type(e.get('session')) is rt.DaySession else None
    preparation_commands=sum(c.get('kind')=='new_synthetic_preparation_command' for c in session['commands']) if session is not None else None
    logical_clock=session.get('clock') if session is not None else None
    return dict(schema=SCHEMA+'.failure',window=window,stage=stage,target=target,
        error=error,actual_created_at_utc=utc_now(),process_launch_attempts=e.get('launch_attempts',0),
        calls_attempted=e['calls'],verified_solver_calls=e['verified_solver_calls'],
        native_solver_call_count_uncertain=uncertain,stage_occurrences=deepcopy(occurrences),
        held_RT_windows=len(e['rt_caps']),accepted_price_windows=len(e['price_caps']),committed_windows=len(e['accepted']),
        issued_RT_cells=3*len(e['accepted']),executed_RT_cells=sum(r['RT'] for r in e.get('receipts',[])),
        preparation_commands=preparation_commands,executed_preparation_cells=sum(not r['RT'] for r in e.get('receipts',[])),logical_clock=logical_clock,
        retry_allowed=False,evidence_preserved=True,policy_repair_allowed=False,stopped=True)


def _stop_run(run,exc,*,window=None,stage=None,target=None):
    e=_run(run);e['stopped']=True
    if e.get('failure') is not None:return
    e['failure']=failure_snapshot(run,error=f'{type(exc).__name__}: {exc}',window=window,stage=stage,target=target)
    name=f'window{window}/failure.json' if window is not None else 'advance_failure_'+uuid4().hex+'.json'
    try:_write(e['output'],name,e['failure'])
    except Exception as write_error:
        # Keep the exact first failure in memory if storage itself failed. Never
        # delete evidence or bypass its cap just to claim a checkpoint succeeded.
        e['failure']['failure_checkpoint_persisted']=False
        e['failure']['failure_checkpoint_error']=f'{type(write_error).__name__}: {write_error}'
        _emit_failure_snapshot(e['failure'])



def _emit_failure_snapshot(failure):
    """Bounded stderr fallback if evidence storage itself fails; never retry.

    The parent must preserve runner stderr. No primal/dual arrays are emitted.
    Exact valid stage/window, latest PID/outcome and all counts survive even
    when an oversized exception requires a compact fallback representation.
    """
    cap=WORKER_FILE_CAP-64;payload=deepcopy(failure);raw=canonical(payload)
    if len(raw)>cap:
        keep=('schema','window','stage','target','actual_created_at_utc','process_launch_attempts',
              'calls_attempted','verified_solver_calls','native_solver_call_count_uncertain',
              'held_RT_windows','accepted_price_windows','committed_windows','issued_RT_cells',
              'executed_RT_cells','preparation_commands','executed_preparation_cells','logical_clock','retry_allowed','evidence_preserved','policy_repair_allowed','stopped',
              'failure_checkpoint_persisted')
        payload={k:failure.get(k) for k in keep}
        payload.update(full_failure_sha256=digest(failure),bounded_stderr_fallback=True,
            original_occurrence_count=len(failure.get('stage_occurrences',[])),
            stage_occurrences=deepcopy(failure.get('stage_occurrences',[])[-1:]))
        for container in [payload]+payload['stage_occurrences']:
            for k,v in list(container.items()):
                if isinstance(v,str) and len(v)>4096:
                    container[k]=v[:4096];container[k+'_full_sha256']=digest(v)
        payload['error_excerpt']=str(failure.get('error',''))[:4096]
        payload['checkpoint_error_excerpt']=str(failure.get('failure_checkpoint_error',''))[:4096]
        raw=canonical(payload)
    require(len(raw)<=cap,'bounded failure fallback invariant')
    try:print('R18_FAILURE_SNAPSHOT '+raw.decode('utf-8'),file=sys.stderr,flush=True)
    except Exception:pass


def _storage_report(e):
    try:return _space(e['output'])
    except Exception as exc:
        if not e['stopped']:raise
        return dict(error=f'{type(exc).__name__}: {exc}',evidence_preserved=True)


def _solve_stage(run,index,stage,inp,model,model_audit,diagnostic=None,*,ticket):
    e=_run(run);require(not e['stopped'],'failed run cannot cross native boundary')
    try:return _solve_stage_impl(run,index,stage,inp,model,model_audit,diagnostic,ticket=ticket,_entry=_NATIVE_ENTRY_KEY)
    except BaseException as exc:
        _stop_run(run,exc,window=index,stage=stage)
        raise


def run_priced_window(run,index):
    e=_run(run);require(not e['stopped'],'failed priced run permanently stopped')
    try:
        require(type(index) is int and 0<=index<96 and index==len(e['accepted']) and e['calls']==2*index,'next complete priced window required')
        e['phase']='PREPARE'
        validate_release(e['release_path'],e['release_sha256']);s=rt._session(e['session'])
        inp,ticket=rt.prepare_window(e['session'],index);model,audit=rt.build_rt_model(inp,ticket)
        e['phase']='RT'
        solved=_solve_stage(run,index,'RT',inp,model,audit,ticket=ticket);rt._ticket(inp,ticket)
        e['phase']='RT_ACCEPT'
        cells,terminal=reconstruct_solution_cells(inp,solved['names'],solved['raw']['primal'])
        out=e['output'];relative=solved['relative'];_write(out,relative+'/solution_cells.json',cells)
        binding=dict(schema=SCHEMA+'.accepted_RT',run_id=e['id'],transaction_id=inp['transaction_id'],occurrence=solved['occurrence'],window=deepcopy(inp['window']),
            input_sha256=digest(inp),solution_cells_sha256=digest(cells),request_sha256=file_sha(solved['directory']/'request.json'),response_sha256=file_sha(solved['directory']/'response.json'),names_sha256=file_sha(solved['directory']/'names.json'),certificate_sha256=file_sha(solved['directory']/'full_certificate.json'),
            source_tree_sha256=e['seal']['tree_sha256'],policy_sha256=digest(pricing.POLICY),runtime_identity=deepcopy(e['release']['runtime_identity']),objective_yuan=solved['certificate']['primal_objective'],full_horizon_cells=24,dispatch_prefix_cells=3,accepted_before_commit=True,official_market=False,execution_authority=False,actual_telemetry=False)
        _write(out,relative+'/acceptance.json',binding)
        data=dict(input=deepcopy(inp),cells=deepcopy(cells),binding=binding);cap=AcceptedRTWindow()
        files={str(p):file_sha(p) for p in solved['directory'].iterdir() if p.is_file()}
        _RT[cap]=dict(run=run,data=data,seal=digest(data),files=files);e['rt_caps'].append(cap)
        source=accepted_rt_source(cap);original,market=rt.contract_sources(s['plan'],s['permit'])
        e['phase']='PRICE_PREPARE'
        pi,price_ticket=pricing.prepare_price_inputs(source['input'],source['cells'],s['plan'],market,original,source['binding'],rt_ticket=ticket,rt_cap=cap,run=run)
        pm,pa=pricing.build_price_model(pi,price_ticket)
        e['phase']='PRICE'
        price=_solve_stage(run,index,'PRICE',pi,pm,pa,pricing.check_selected_reference_witness,ticket=price_ticket)
        e['phase']='PRICE_ACCEPT'
        pricing._price_ticket(pi,price_ticket)
        accepted_rt_source(cap);rt._ticket(inp,ticket);contract.validate_contract(s['plan'],s['permit'],recheck_files=True)
        price_cells,price_terminal=pricing.reconstruct_price_cells(pi,price['names'],price['raw']['primal'])
        _write(out,price['relative']+'/solution_cells.json',dict(cells=price_cells,terminal_state=price_terminal,dispatch_executable=False,origin='independent_PRICE_lift_not_RT_command'))
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
        from .gd_r18_energy_ledger import freeze_first_quarter,ledger_report
        e['phase']='FREEZE'
        frozen=freeze_first_quarter(e['ledger'],pcap,cap,run)
        e['phase']='COMMIT'
        commands=[dict(id=s['id']+f'-W{index}-RT-{j}',kind='accepted_fresh_RT_research_command',issued_at=inp['window']['cutoff'],available_at=inp['window']['cutoff'],occurrence_id=binding['occurrence']['id'],acceptance_sha256=digest(binding),cell=deepcopy(c)) for j,c in enumerate(cells[:3])]
        _write(out,f'window{index}/frozen_first_quarter.json',frozen);_write(out,f'window{index}/issued_commands.json',commands)
        # Persist compact freeze/command deltas, not96 cumulative ledger copies.
        # First state commit occurs only after both complete witnesses and freeze persist.
        s['commands'].extend(commands);s['accepted_windows'].append(binding);s['next_window']+=1;s['prepared']=None;rt._TICKETS[ticket]['consumed']=True
        accepted=dict(window=deepcopy(inp['window']),RT=deepcopy(binding),PRICE=deepcopy(result),first_quarter_freeze_sha256=digest(frozen),issued_RT_cells=3)
        e['accepted'].append(accepted);rt._seal_session(e['session']);e['phase']='COMMITTED';return deepcopy(accepted)
    except BaseException as exc:
        _stop_run(run,exc,window=index,stage=e.get('phase','PREPARE'))
        raise


def _advance_to_impl(run,target):
    e=_run(run);require(not e['stopped'],'failed run cannot advance');s=rt._session(e['session']);target=stamp(target)
    allowed={stamp(w['start']) for w in s['specs']}|{stamp(s['specs'][-1]['delivery_end'])}
    require(target in allowed and target>stamp(s['clock']),'declared forward boundary pause/drain required')
    require(e['accepted'], 'cannot advance before a committed RT/PRICE window')
    authenticate_projection(run,e['session'])
    expected = stamp(s['specs'][len(e['accepted'])-1]['start'])
    if stamp(s['clock']) == expected and len(e['accepted']) == 96:
        expected = stamp(s['specs'][-1]['delivery_end'])
    require(target == expected, 'next committed start boundary or final W95 drain only')
    state=deepcopy(s['receipt']['state']);receipts=[]
    while stamp(state['at'])<target:
        matches=[c for c in s['commands'] if c['cell']['start']==state['at'] and stamp(c['issued_at'])<=stamp(c['available_at'])<=stamp(c['cell']['start'])]
        require(len(matches)==1,'missing/duplicate command at execution boundary');c=matches[0]
        require(c['id'] not in e['executed_ids'] and stamp(c['cell']['end'])<=target,'duplicate/future receipt')
        state=integrate_cell(state,c['cell'],s['plan']['technical'])
        receipts.append(dict(id=c['id']+'-SIMULATED-RECEIPT',at=state['at'],available_at=state['at'],state=deepcopy(state),command_id=c['id'],command_sha256=digest(c),is_measurement=False,kind='simulated_zero_tracking_execution_not_telemetry',RT=c['kind']=='accepted_fresh_RT_research_command'))
    _write(e['output'],f'receipts_{target.strftime("%Y%m%d_%H%M")}.json',dict(target=target.isoformat(),receipts=receipts))
    e['receipts'].extend(receipts);e['executed_ids'].update(r['command_id'] for r in receipts)
    if receipts:s['receipt']=deepcopy(receipts[-1])
    s['clock']=target.isoformat();rt._seal_session(e['session'])
    from .gd_r18_energy_ledger import record_available_quarters,ledger_report
    written=record_available_quarters(e['ledger'],run)
    _write(e['output'],f'quarter_deltas_{target.strftime("%Y%m%d_%H%M")}.json',written)
    if target.minute==0 and target>stamp(s['specs'][0]['start']):
        ledger=ledger_report(e['ledger']);hour=(int((target-stamp(s['specs'][0]['start'])).total_seconds())//3600)-1
        keys=[k for k,v in ledger['frozen_prices'].items() if v['window_index']//4==hour]
        _write(e['output'],f'hour{hour:02d}_checkpoint.json',dict(hour_index=hour,logical_clock=s['clock'],state=deepcopy(s['receipt']['state']),frozen_prices={k:ledger['frozen_prices'][k] for k in keys},executed_quarters={k:ledger['executed_quarters'][k] for k in keys},hourly_component=ledger['hourly_components'][hour]))
    return compact_report(run)


def advance_to(run,target):
    e=_run(run);require(not e['stopped'],'failed run cannot advance')
    try:return _advance_to_impl(run,target)
    except BaseException as exc:
        _stop_run(run,exc,target=str(target),stage='ADVANCE')
        raise


def report(run):
    e=_run(run);s=rt._session(e['session'])
    from .gd_r18_energy_ledger import ledger_report
    return dict(schema=SCHEMA+'.report',status='stopped' if e['stopped'] else 'research_session',actual_created_at_utc=e['created_at_utc'],logical_clock=s['clock'],
        held_audited_RT_windows=len(e['rt_caps']),accepted_RT_windows=len(e['accepted']),accepted_price_windows=len(e['price_caps']),issued_RT_cells=3*len(e['accepted']),executed_RT_cells=sum(r['RT'] for r in e['receipts']),
        preparation_commands=3,executed_preparation_cells=sum(not r['RT'] for r in e['receipts']),native_process_calls=e['calls'],verified_native_solver_calls=e['verified_solver_calls'],
        stage_occurrences=deepcopy(e['occurrences']),new_preparation_calls=0,new_DA_calls=0,independent_RT_price_calls=sum(o['stage']=='PRICE' for o in e['occurrences']),
        formal_bill=None,official_price=None,actual_telemetry=False,official_market=False,execution_authority=False,price_dispatch_executable=False,
        native_solver_call_count_uncertain=_native_uncertain(e),accepted=deepcopy(e['accepted']),ledger=ledger_report(e['ledger']),storage=_storage_report(e),failure=deepcopy(e.get('failure')),process_launch_attempts=e.get('launch_attempts',0))


def authenticate_projection(run,session):
    """Authenticate complete causal lineage; pure R17 flags cannot substitute.

    Every issued prefix is exactly three cells of a registered fresh RT
    capability whose independent PRICE froze first. Every available receipt
    is re-integrated from the archived seed, not trusted for its timestamp.
    """
    e=_run(run);s=rt._session(session)
    require(e['session'] is session and s['run'] is run and not e['stopped'],'same running R18 session required')
    require(len(e['accepted'])==s['next_window']==len(s['accepted_windows']),'live accepted count mismatch')
    require(_path_intact() and source_manifest()==e['seal'],'projection source/path changed')
    prep=s['plan']['initial_preparation'];commands=s['commands']
    require(len(commands)==3+3*len(e['accepted']) and len({c['id'] for c in commands})==len(commands),'issued dense prefix required')
    for j,c in enumerate(commands[:3]):
        require(c['kind']=='new_synthetic_preparation_command' and c['id']==s['id']+'-PREP-'+str(j) and digest(c['cell'])==digest(prep['cells'][j]) and c['source_bindings_sha256']==digest(s['plan']['preparation_source_bindings']),'preparation source binding changed')
    for i,accepted in enumerate(e['accepted']):
        rc,pc=e['rt_caps'][i],e['price_caps'][i]
        rcap=_cap_in_verified_run(rc,_RT,'RT',run);pcap=_cap_in_verified_run(pc,_PRICE,'PRICE',run)
        require(pcap['rt_cap'] is rc,'projection PRICE/RT capability pair changed')
        source=rcap['data'];binding=source['binding']
        require(pcap['data']['source_binding_sha256']==digest(binding),'projection price/source binding changed')
        require(digest(binding)==digest(s['accepted_windows'][i])==digest(accepted['RT']),'accepted window lineage changed')
        for j,c in enumerate(commands[3+3*i:6+3*i]):
            require(c['id']==binding['transaction_id']+f'-RT-{j}' and c['kind']=='accepted_fresh_RT_research_command' and c['occurrence_id']==binding['occurrence']['id'] and c['acceptance_sha256']==digest(binding) and digest(c['cell'])==digest(source['cells'][j]),'command not from same accepted RT prefix')
            require(c['issued_at']==c['available_at']==s['specs'][i]['cutoff'],'issued command clock changed')
    receipts=e['receipts'];require(len({r['id'] for r in receipts})==len(receipts) and len({r['command_id'] for r in receipts})==len(receipts),'duplicate live receipt')
    state=deepcopy(prep['seed_state']);clock=stamp(s['clock']);expected=[]
    for c in commands:
        if stamp(c['cell']['end'])>clock:break
        state=integrate_cell(state,c['cell'],s['plan']['technical'])
        rs=[r for r in receipts if r['command_id']==c['id']];require(len(rs)==1,'missing causal receipt')
        r=rs[0]
        require(r['id']==c['id']+'-SIMULATED-RECEIPT' and r['command_sha256']==digest(c) and r['at']==r['available_at']==state['at'] and digest(r['state'])==digest(state) and r['is_measurement'] is False and r['kind']=='simulated_zero_tracking_execution_not_telemetry' and r['RT']==(c['kind']=='accepted_fresh_RT_research_command'),'complete receipt state/command binding mismatch')
        expected.append(r)
    require(len(expected)==len(receipts) and set(e['executed_ids'])=={r['command_id'] for r in receipts},'future/extra execution receipt')
    if expected:require(digest(s['receipt'])==digest(expected[-1]),'current receipt is not live latest available receipt')
    else:require(s['receipt']['kind']=='new_synthetic_seed_from_reverified_archived_preparation' and digest(s['receipt']['state'])==digest(prep['seed_state']),'seed receipt changed')
    require(stamp(state['at'])==clock,'causal live state not at cutoff')
    require(_path_intact() and source_manifest()==e['seal'],'source/path changed during projection audit')
    return dict(run_id=e['id'],session_id=s['id'],receipt_sha256=digest(s['receipt']),issued_commands_sha256=digest(commands),accepted_windows=len(e['accepted']),cutoff=s['clock'],contract_sha256=digest(s['plan']))


def compact_report(run):
    e=_run(run);s=rt._session(e['session'])
    from .gd_r18_energy_ledger import compact_ledger_report
    return dict(schema=SCHEMA+'.compact_report',logical_clock=s['clock'],stopped=e['stopped'],
        accepted_RT_windows=len(e['accepted']),accepted_price_windows=len(e['price_caps']),
        issued_RT_cells=3*len(e['accepted']),executed_RT_cells=sum(r['RT'] for r in e['receipts']),
        native_process_calls=e['calls'],verified_native_solver_calls=e['verified_solver_calls'],
        ledger=compact_ledger_report(e['ledger']),formal_bill=None)


from . import gd_r18_energy_ledger as energy_ledger
from . import gd_r18_day_models as day_models
_REFERENCES=[(subprocess,'Popen',subprocess.Popen),(subprocess.Popen,'wait',subprocess.Popen.wait),(hs,'serialize_model',hs.serialize_model),
    (rt,'build_rt_model',rt.build_rt_model),(rt,'prepare_window',rt.prepare_window),
    (pricing,'build_price_model',pricing.build_price_model),(pricing,'normalize_price_inputs',pricing.normalize_price_inputs),
    (pricing,'audit_independent_offer_cost',pricing.audit_independent_offer_cost),(pricing,'independent_offer_cost',pricing.independent_offer_cost),(pricing,'validate_price_inputs',pricing.validate_price_inputs),(pricing,'derive_quarters',pricing.derive_quarters),(pricing,'_curve',pricing._curve),
    (energy_ledger,'freeze_first_quarter',energy_ledger.freeze_first_quarter),(energy_ledger,'record_available_quarters',energy_ledger.record_available_quarters),(energy_ledger,'validate_quarter_receipts',energy_ledger.validate_quarter_receipts),
    (pricing,'extract_lmps',pricing.extract_lmps),(pricing,'check_selected_reference_witness',pricing.check_selected_reference_witness)]
_REFERENCES += [(energy_ledger,name,getattr(energy_ledger,name)) for name in ('quarter_energy_rows','_hourly_arithmetic')]
_REFERENCES += [(module,name,fn) for module in (rt,pricing,energy_ledger,bridge,pure,contract,day_models) for name,fn in vars(module).items() if callable(fn)]
_ORIGINALS={name:fn for name,fn in list(globals().items()) if callable(fn)}
_ORIGINALS.update({name:globals()[name] for name in ('source_manifest','_write','_space','audit_lp_certificate','reconstruct_solution_cells','runtime_identity')})
