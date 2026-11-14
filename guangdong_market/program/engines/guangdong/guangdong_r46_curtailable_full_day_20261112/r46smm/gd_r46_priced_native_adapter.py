"""New bounded 96-window research transaction with independent raw PRICE.

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
from . import gd_r46_day_session as rt
from . import gd_r15_da_rt_bridge as bridge
from . import gd_r46_source_contract as contract
from . import gd_r41_static_stage as static_stage_checks
from . import gd_r46_rt_pricing as pricing
from . import gd_r46_price_terminal as terminal
from . import gd_r46_vre as vre
from . import gd_r46_native_support as pure
from . import highs_solver as hs
from .gd_r46_native_identity import runtime_identity
from .gd_r15_da_rt_bridge import canonical,digest,file_sha,read_json,require,stamp,integrate_cell

SCHEMA='gd.research.r46.priced_full_day.v1'
OPTIONS={stage:dict(threads=1,time_limit=limit,mip_rel_gap=.001,mip_abs_gap=0.,output_flag=False,presolve='choose',solve_relaxation=False) for stage,limit in [('RT',120.),('PRICE',60.)]}
ORDER=[stage+str(i) for i in range(96) for stage in ('RT','PRICE')]
PENDING_TERMINAL_ERRORS=frozenset(('translated PRICE terminal outside original SOC hard bounds; no clipping','translated PRICE terminal outside necessary reachable interval; no repair'))
TERMINAL_POLICY_SHA256=digest(terminal.POLICY)
POLICY_SHA256=digest(pricing.POLICY)
WINDOW_POLICY_SHA256=digest(rt.WINDOW_POLICY)
CONTRACT_POLICY_SHA256=digest(contract.POLICY)
EVIDENCE_CAP=pure.EVIDENCE_CAP;FILE_CAP=pure.FILE_CAP;DISK_FLOOR=pure.DISK_FLOOR
WORKER_FILE_CAP=1024**2
ESTIMATE_PATH=Path(__file__).with_name('evidence_budget.json')
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
        and digest(terminal.POLICY)==TERMINAL_POLICY_SHA256
        and ORDER==[stage+str(i) for i in range(96) for stage in ('RT','PRICE')] and digest(pricing.POLICY)==POLICY_SHA256 and digest(rt.WINDOW_POLICY)==WINDOW_POLICY_SHA256 and digest(contract.POLICY)==CONTRACT_POLICY_SHA256 and pure._path_intact()
        and all(getattr(obj,name) is fn for obj,name,fn in _REFERENCES)
        and all(globals()[name] is fn for name,fn in _ORIGINALS.items()))


validate_evidence_budget=pure.validate_evidence_budget
_write=pure._write


def validate_release(path,expected_sha256):
    path=Path(path).resolve();require(file_sha(path)==expected_sha256,'run plan hash mismatch');r=read_json(path)
    require(r['schema']==SCHEMA+'.run_plan' and r['approved'] is True,'explicit new R46 run plan required')
    require(r['start_index']==0 and r['window_count']==96,'only fresh W0-95 full day')
    require(r['max_native_calls']==2*r['window_count'] and r['solver_options']==OPTIONS,'bounded unchanged solver settings required')
    require(r['price_missing_reasons']==['confirmed_PRICE_Infeasible','research_terminal_domain_unavailable'],'exact missing reason whitelist required')
    require(r['policy_sha256']==POLICY_SHA256 and r['window_policy_sha256']==WINDOW_POLICY_SHA256 and r['price_terminal_policy_sha256']==TERMINAL_POLICY_SHA256,'policy pin mismatch')
    require(r['formal_strict'] is False and r['retry_allowed'] is False,'research only; no retry')
    seal=source_manifest();require(r['source_tree_sha256']==seal['tree_sha256'] and _path_intact(),'run source/path changed')
    require(r['runtime_identity']==runtime_identity(),'runtime changed')
    require(r['runtime_identity']['packages']['highspy']['version']=='1.15.1','required HiGHS1.15.1')
    require(r.get('simulation_runner_authorization') is True and r.get('execution_authority') is False,'local research only plan')
    require(r['serialized_evidence_estimate_sha256']==file_sha(ESTIMATE_PATH),'budget changed')
    validate_evidence_budget(read_json(ESTIMATE_PATH));return r,seal

def create_priced_run(*,r14_core,preparation_root,output,release_path,release_sha256,seed_root=None):
    release,seal=validate_release(release_path,release_sha256);out=Path(output).resolve();source=Path(__file__).resolve().parents[1]
    require(not out.exists() and not out.is_relative_to(source),'new external evidence required')
    plan,permit=contract.load_contract(r14_core,preparation_root=preparation_root,policy=contract.POLICY)
    require(release['contract_sha256']==digest(plan),'source contract changed')
    session=rt.initialize_day(plan,permit);seed=plan['seed_proof']
    require(seed_root is None and release['seed_sha256']==digest(seed),'new midnight preparation seed only; no old RT history')
    out.mkdir(parents=True,exist_ok=False);_space(out);run=PricedResearchRun();session_data=rt._session(session)
    _RUNS[run]=dict(id='R46-DISPATCH-'+uuid4().hex,session=session,output=out,seal=seal,release=release,
        release_path=Path(release_path).resolve(),release_sha256=release_sha256,calls=0,verified_solver_calls=0,launch_attempts=0,failure=None,phase='INITIALIZED',
        stopped=False,accepted=[],rt_caps=[],price_caps=[],receipts=[],executed_ids=set(),occurrences=[],price_records=[],price_evidence=[],post_verification=None,
        completed_quarters=[],start_index=release['start_index'],end_index=release['start_index']+release['window_count'],seed_proof=seed,
        initial_receipt=deepcopy(session_data['receipt']),initial_commands=deepcopy(session_data['commands']),created_at_utc=utc_now())
    rt._SESSIONS[session]['run']=run
    _write(out,'source_manifest.json',seal);_write(out,'release.json',release);_write(out,'publication.json',plan)
    _write(out,'pricing_policy.json',pricing.POLICY);_write(out,'pre_native_evidence_budget.json',read_json(ESTIMATE_PATH));_write(out,'initial_session.json',report(run))
    if seed is not None:_write(out,'midnight_preparation_seed_proof.json',seed)
    return run

def _run(run):
    require(type(run) is PricedResearchRun and run in _RUNS,'fresh priced run capability required')
    return _RUNS[run]


def _cap_in_verified_run(cap,registry,kind,run=None):
    """Check capability and all native files; caller separately seals source."""
    cls=AcceptedRTWindow if kind=='RT' else AcceptedPriceWindow
    require(type(cap) is cls and cap in registry,'registered fresh R46 '+kind+' capability required')
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
    require(r['run'] is run,'same live R46 RT run required')
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
    expected=dict(id=envelope['occurrence_id'],stage=stage,worker_pid=pid,request_sha256=file_sha(request_path),names_sha256=file_sha(names_path),source_tree_sha256=envelope['source_tree_sha256'],input_sha256=envelope['input_sha256'],policy_sha256=envelope['policy_sha256'],worker_file_sha256=envelope['worker_file_sha256'],native_run_count=1,runtime_identity=envelope['runtime_identity'],window_index=envelope['window_index'],stage_ordinal=envelope['stage_ordinal'],native_call_ordinal=envelope['native_call_ordinal'],run_id=envelope['run_id'],contract_sha256=envelope['contract_sha256'],window_policy_sha256=envelope['window_policy_sha256'],price_terminal_policy_sha256=TERMINAL_POLICY_SHA256,formal_strict=False)
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
    require(e['calls']<e['release']['max_native_calls'] and e['start_index']<=index<e['end_index'],'bounded run stage scope')
    require(e['phase']==stage and not any(o['stage']==stage and o['index']==index for o in e['occurrences']),'no duplicate stage or implicit retry')
    require(_path_intact() and source_manifest()==e['seal'],'stage source/path changed')
    out=e['output'];relative=f'window{index}/{stage.lower()}';directory=out/relative;directory.mkdir(parents=True,exist_ok=False)
    opts=OPTIONS[stage];payload,vs,cs=hs.serialize_model(model,opts);names=dict(variables=[v.name for v in vs],constraints=[c.name for c in cs])
    require(all(v['kind']=='C' for v in payload['variables']),'continuous LP only')
    budget=read_json(ESTIMATE_PATH)['stages'][stage+str(index)]
    require(len(payload['variables'])<=budget['columns'] and len(payload['rows'])<=budget['rows'],'reviewed stage shape changed')
    require(len(canonical(inp))<=budget['input_bound_bytes'] and len(canonical(payload))<=budget['request_bound_bytes'] and len(canonical(names))<=budget['names_bound_bytes'],'serialized stage exceeds reviewed estimate')
    _write(out,relative+'/input.json',inp);nh=_write(out,relative+'/names.json',names);_write(out,relative+'/model_audit.json',model_audit)
    if diagnostic is not None:_write(out,relative+'/selected_RT_diagnostic.json',diagnostic(inp,payload,names))
    started=time.perf_counter();payload.update(deadline=started+opts['time_limit'],preparation_elapsed_s=0.)
    worker=Path(__file__).with_name('gd_r46_priced_native_worker.py')
    envp=dict(schema=SCHEMA+'.request',price_terminal_policy_sha256=TERMINAL_POLICY_SHA256,formal_strict=False,stage=stage,window_index=index,stage_ordinal=2*index+(stage=='PRICE'),native_call_ordinal=e['calls'],run_id=e['id'],contract_sha256=e['release']['contract_sha256'],window_policy_sha256=digest(rt.WINDOW_POLICY),occurrence_id='R46-'+stage+'-'+uuid4().hex,input_sha256=digest(inp),source_tree_sha256=e['seal']['tree_sha256'],names_sha256=nh,worker_file_sha256=file_sha(worker),policy_sha256=digest(pricing.POLICY),created_at_utc=utc_now(),runtime_identity=deepcopy(e['release']['runtime_identity']),payload=payload)
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
    if stage=='PRICE' and raw.get('native_status')=='Infeasible' and raw.get('status')=='infeasible' and raw.get('has_primal') is False:
        return dict(directory=directory,relative=relative,names=names,raw=raw,certificate=None,occurrence=occurrence,payload=payload,confirmed_infeasible=True)
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
    try:print('R46_FAILURE_SNAPSHOT '+raw.decode('utf-8'),file=sys.stderr,flush=True)
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
        require(type(index) is int and 0<=index<96 and index==e['start_index']+len(e['accepted']) and index<e['end_index'],'next complete priced window required')
        e['phase']='PREPARE'
        validate_release(e['release_path'],e['release_sha256']);s=rt._session(e['session'])
        with contract.native_stage_scope(s['plan'],s['permit'],run_id=e['id'],index=index,stage='RT') as rt_static_check:
            inp,ticket=rt.prepare_window(e['session'],index);model,audit=rt.build_rt_model(inp,ticket)
            e['phase']='RT'
            solved=_solve_stage(run,index,'RT',inp,model,audit,ticket=ticket);rt._ticket(inp,ticket)
        _write(e['output'],f'window{index}/rt/static_source_boundaries.json',rt_static_check)
        e['phase']='RT_ACCEPT'
        cells,rt_terminal_state=reconstruct_solution_cells(inp,solved['names'],solved['raw']['primal'])
        out=e['output'];relative=solved['relative'];_write(out,relative+'/solution_cells.json',cells)
        binding=dict(schema=SCHEMA+'.accepted_RT',run_id=e['id'],transaction_id=inp['transaction_id'],occurrence=solved['occurrence'],window=deepcopy(inp['window']),
            input_sha256=digest(inp),solution_cells_sha256=digest(cells),request_sha256=file_sha(solved['directory']/'request.json'),response_sha256=file_sha(solved['directory']/'response.json'),names_sha256=file_sha(solved['directory']/'names.json'),certificate_sha256=file_sha(solved['directory']/'full_certificate.json'),
            source_tree_sha256=e['seal']['tree_sha256'],policy_sha256=digest(pricing.POLICY),runtime_identity=deepcopy(e['release']['runtime_identity']),objective_yuan=solved['certificate']['primal_objective'],full_horizon_cells=24,dispatch_prefix_cells=3,accepted_before_commit=True,official_market=False,execution_authority=False,actual_telemetry=False)
        _write(out,relative+'/acceptance.json',binding)
        data=dict(input=deepcopy(inp),cells=deepcopy(cells),binding=binding);cap=AcceptedRTWindow()
        files={str(p):file_sha(p) for p in solved['directory'].iterdir() if p.is_file()}
        _RT[cap]=dict(run=run,data=data,seal=digest(data),files=files);e['rt_caps'].append(cap)
        source=accepted_rt_source(cap);original,market=rt.contract_sources(s['plan'],s['permit'])
        e['phase']='RT_COMMIT'
        commands=[dict(id=s['id']+f'-W{index}-RT-{j}',kind='accepted_fresh_RT_research_command',issued_at=inp['window']['cutoff'],available_at=inp['window']['cutoff'],occurrence_id=binding['occurrence']['id'],acceptance_sha256=digest(binding),cell=deepcopy(c)) for j,c in enumerate(cells[:3])]
        _write(out,f'window{index}/issued_commands.json',commands)
        s['commands'].extend(commands);s['accepted_windows'].append(binding);s['next_window']+=1
        accepted=dict(window=deepcopy(inp['window']),RT=deepcopy(binding),issued_RT_cells=3,PRICE_status='pending_processing')
        e['accepted'].append(accepted);rt._seal_session(e['session'])
        e['phase']='PRICE_PREPARE'
        with contract.native_stage_scope(s['plan'],s['permit'],run_id=e['id'],index=index,stage='PRICE') as price_static_check:
            pending=None
            try:terminal.terminal_trace(source['input'])
            except ValueError as exc:
                if str(exc) not in PENDING_TERMINAL_ERRORS:raise
                pending=dict(reason='research_terminal_domain_unavailable',detail=str(exc),native_price_called=False)
            if pending is None:
                pi,price_ticket=pricing.prepare_price_inputs(source['input'],source['cells'],s['plan'],market,original,source['binding'],rt_ticket=ticket,rt_cap=cap,run=run)
                pm,pa=pricing.build_price_model(pi,price_ticket);e['phase']='PRICE'
                price=_solve_stage(run,index,'PRICE',pi,pm,pa,pricing.check_selected_reference_witness,ticket=price_ticket)
                if price.get('confirmed_infeasible'):
                    pending=dict(reason='confirmed_PRICE_Infeasible',detail='verified native Infeasible, no primal',native_price_called=True,occurrence=deepcopy(price['occurrence']))
            if pending is None:
                e['phase']='PRICE_ACCEPT';pricing._price_ticket(pi,price_ticket)
                price_cells,price_terminal=pricing.reconstruct_price_cells(pi,price['names'],price['raw']['primal'])
                _write(out,price['relative']+'/solution_cells.json',dict(cells=price_cells,terminal_state=price_terminal,dispatch_executable=False,origin='independent_PRICE_lift_not_RT_command'))
                direct=pricing.audit_independent_offer_cost(pi,price['names'],price['raw']);_write(out,price['relative']+'/direct_original_offer_cost.json',direct)
                result=pricing.extract_lmps(pi,price['names'],price['raw']);result['cost_comparison']=direct
        _write(out,f'window{index}/price/static_source_boundaries.json',price_static_check)
        if pending is not None:
            rec=dict(window=deepcopy(inp['window']),status='pending',nodes=deepcopy(inp['nodes']),run_id=e['id'],native_outcome='known',
                rt_binding_sha256=digest(binding),input_sha256=digest(inp),mapping_is_research=True,**pending)
            _write(out,f'window{index}/price/pending.json',rec)
        else:
            result.update(schema=SCHEMA+'.accepted_PRICE',run_id=e['id'],window=deepcopy(inp['window']),source_binding_sha256=digest(binding),
                model_sha256=digest({k:price['payload'][k]for k in ('variables','rows','objective','options')}),input_sha256=digest(pi),
                policy_sha256=digest(pricing.POLICY),occurrence=price['occurrence'],objective_yuan=price['certificate']['primal_objective'],
                actual_created_at_utc=utc_now(),temporary_price=True,official_publication=None,formal_bill=None)
            _write(out,price['relative']+'/acceptance.json',result);pcap=AcceptedPriceWindow()
            _PRICE[pcap]=dict(run=run,rt_cap=cap,data=result,seal=digest(result),files={str(p):file_sha(p)for p in price['directory'].iterdir()if p.is_file()});e['price_caps'].append(pcap)
            rec={k:deepcopy(result[k])for k in ('window','prices','occurrence','input_sha256','model_sha256','run_id')};rec.update(status='success',source_artifacts={str(p.relative_to(out)):file_sha(p)for p in price['directory'].iterdir()if p.name in ('request.json','response.json','acceptance.json','names.json')})
        price_dir=out/f'window{index}/price'
        e['price_evidence'].append({str(p):file_sha(p)for p in price_dir.iterdir()if p.is_file()})
        e['price_records'].append(rec);accepted['PRICE_status']=rec['status']
        s['prepared']=None;rt._TICKETS[ticket]['consumed']=True;rt._seal_session(e['session']);e['phase']='COMMITTED'
        return deepcopy(accepted)
    except BaseException as exc:
        _stop_run(run,exc,window=index,stage=e.get('phase','PREPARE'))
        raise


def _advance_to_impl(run,target):
    e=_run(run);require(not e['stopped'],'failed run cannot advance');s=rt._session(e['session']);target=stamp(target)
    allowed={stamp(w['start']) for w in s['specs']}|{stamp(s['specs'][e['end_index']-1]['delivery_end'])}
    require(target in allowed and target>stamp(s['clock']),'declared forward boundary pause/drain required')
    require(e['accepted'], 'cannot advance before a committed RT/PRICE window')
    authenticate_projection(run,e['session'])
    expected = stamp(s['specs'][e['start_index']+len(e['accepted'])-1]['start'])
    if stamp(s['clock']) == expected and len(e['accepted']) == e['release']['window_count']:
        expected = stamp(s['specs'][e['end_index']-1]['delivery_end'])
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
    # Completed physical quarters do not require any successful price.
    from .gd_r19_energy_ledger import resource_nodes,validate_quarter_receipts
    completed={z['window']['index'] for z in e['completed_quarters']}
    for j,a in enumerate(e['accepted']):
        if a['window']['index'] in completed or stamp(a['window']['delivery_end'])>target:continue
        source=accepted_rt_source(e['rt_caps'][j]);binding=source['binding']
        chosen_commands,chosen_receipts=validate_quarter_receipts(commands=s['commands'],receipts=e['receipts'],cells=source['cells'][:3],window=a['window'],binding=binding,logical_clock=s['clock'])
        e['completed_quarters'].append(dict(command_ids=[c['id']for c in chosen_commands],receipt_ids=[r['id']for r in chosen_receipts],window=deepcopy(a['window']),RT_cells=deepcopy(source['cells'][:3]),DA_cells=deepcopy(source['input']['da_cells'][:3]),nodes=resource_nodes(source['input']['resources']),execution_verified=True))
    if target.minute==0 and target>stamp(s['plan']['day_start']):_write(e['output'],f"hour{int((target-stamp(s['plan']['day_start'])).total_seconds()/3600)-1:02d}_checkpoint.json",dict(logical_clock=s['clock'],state=deepcopy(s['receipt']['state']),summary=compact_report(run)))
    return compact_report(run)

def advance_to(run,target):
    e=_run(run);require(not e['stopped'],'failed run cannot advance')
    try:return _advance_to_impl(run,target)
    except BaseException as exc:
        _stop_run(run,exc,target=str(target),stage='ADVANCE')
        raise


def report(run):
    e=_run(run);s=rt._session(e['session'])
    return dict(schema=SCHEMA+'.report',status='stopped' if e['stopped'] else 'research_session',logical_clock=s['clock'],
        held_audited_RT_windows=len(e['rt_caps']),accepted_RT_windows=len(e['accepted']),accepted_price_windows=len(e['price_caps']),
        pending_price_windows=sum(z['status']=='pending' for z in e['price_records']),issued_RT_cells=3*len(e['accepted']),executed_RT_cells=sum(r['RT']for r in e['receipts']),
        initial_prefix_kind='original_midnight_research_preparation',prior_rolling_RT_windows=0,financial_price_available=False,market_price=None,settlement_price=None,preparation_commands=3,executed_preparation_cells=sum(not r['RT']for r in e['receipts']),native_process_calls=e['calls'],verified_native_solver_calls=e['verified_solver_calls'],
        native_solver_call_count_uncertain=_native_uncertain(e),stage_occurrences=deepcopy(e['occurrences']),completed_quarters=len(e['completed_quarters']),
        price_records=deepcopy(e['price_records']),post_verification=deepcopy(e['post_verification']),storage=_storage_report(e),failure=deepcopy(e['failure']),
        formal_bill=None,official_price=None,actual_telemetry=False,execution_authority=False,new_DA_calls=0,new_preparation_calls=0)

def authenticate_projection(run,session):
    e=_run(run);s=rt._session(session)
    require(e['session'] is session and s['run'] is run and not e['stopped'],'same live R46 run required')
    require(e['start_index']+len(e['accepted'])==s['next_window'] and len(e['accepted'])==len(s['accepted_windows']),'live RT count mismatch')
    require(_path_intact() and source_manifest()==e['seal'],'source/path changed')
    commands=s['commands'];require(len(commands)==3+3*len(e['accepted']) and len({c['id']for c in commands})==len(commands),'dense issued prefix required')
    require(digest(commands[:3])==digest(e['initial_commands']),'initial prefix mutated')
    for j,a in enumerate(e['accepted']):
        rc=_cap_in_verified_run(e['rt_caps'][j],_RT,'RT',run);source=rc['data'];binding=source['binding']
        require(digest(binding)==digest(s['accepted_windows'][j])==digest(a['RT']),'RT lineage changed')
        for k,c in enumerate(commands[3+3*j:6+3*j]):
            require(c['id']==binding['transaction_id']+f'-RT-{k}' and c['occurrence_id']==binding['occurrence']['id'] and c['acceptance_sha256']==digest(binding) and digest(c['cell'])==digest(source['cells'][k]),'command not from same new RT')
            require(c['issued_at']==c['available_at']==a['window']['cutoff'],'command cutoff mismatch')
    receipts=e['receipts'];require(len({r['id']for r in receipts})==len(receipts) and len({r['command_id']for r in receipts})==len(receipts),'duplicate receipt')
    state=deepcopy(e['initial_receipt']['state']);clock=stamp(s['clock']);expected=[]
    for c in commands:
        if stamp(c['cell']['end'])>clock:break
        require(stamp(c['issued_at'])<=stamp(c['available_at'])<=stamp(c['cell']['start']),'future command provenance')
        state=integrate_cell(state,c['cell'],s['plan']['technical']);rs=[r for r in receipts if r['command_id']==c['id']];require(len(rs)==1,'missing/duplicate receipt');r=rs[0]
        require(r['id']==c['id']+'-SIMULATED-RECEIPT' and r['command_sha256']==digest(c) and r['at']==r['available_at']==state['at'] and digest(r['state'])==digest(state) and r['is_measurement'] is False and r['RT']==(c['kind']=='accepted_fresh_RT_research_command'),'complete causal receipt mismatch');expected.append(r)
    require(len(expected)==len(receipts) and set(e['executed_ids'])=={r['command_id']for r in receipts},'future/extra execution receipt')
    require(digest(s['receipt'])==digest(expected[-1] if expected else e['initial_receipt']),'latest receipt mismatch')
    require(stamp(state['at'])==clock,'cutoff state missing')
    return dict(run_id=e['id'],session_id=s['id'],receipt_sha256=digest(s['receipt']),issued_commands_sha256=digest(commands),accepted_windows=len(e['accepted']),cutoff=s['clock'],contract_sha256=digest(s['plan']))

def compact_report(run):
    e=_run(run);s=rt._session(e['session'])
    return dict(logical_clock=s['clock'],stopped=e['stopped'],RT_committed=len(e['accepted']),PRICE_success=len(e['price_caps']),PRICE_pending=sum(z['status']=='pending' for z in e['price_records']),issued_RT_cells=3*len(e['accepted']),executed_RT_cells=sum(r['RT']for r in e['receipts']),native_calls=e['calls'],verified_native_calls=e['verified_solver_calls'])


def verify_post_research(run):
    e=_run(run);s=rt._session(e['session']);require(not e['stopped'] and len(e['completed_quarters'])==e['release']['window_count'],'all scoped cells must be executed first')
    authenticate_projection(run,e['session'])
    from .gd_r19_energy_ledger import resource_nodes,validate_quarter_receipts
    require(len(e['price_records'])==len(e['accepted'])==len(e['price_evidence']),'price classification coverage mismatch')
    success_index=0
    for j,(rec,a) in enumerate(zip(e['price_records'],e['accepted'])):
        source=accepted_rt_source(e['rt_caps'][j]);inp=source['input'];binding=source['binding']
        require(rec['window']==a['window'] and rec['run_id']==e['id'],'price/physical window binding changed')
        for path,h in e['price_evidence'][j].items():require(file_sha(path)==h,'retained price evidence changed')
        if rec['status']=='success':
            pc=_cap_in_verified_run(e['price_caps'][success_index],_PRICE,'PRICE',run);success_index+=1
            require(pc['rt_cap'] is e['rt_caps'][j],'price RT capability mismatch')
            result=pc['data'];require(all(rec[k]==result[k]for k in ('window','prices','occurrence','input_sha256','model_sha256','run_id')),'success record not from accepted native price')
        else:
            pending_path=e['output']/f"window{a['window']['index']}/price/pending.json"
            require(read_json(pending_path)==rec,'pending record changed')
            if rec['reason']=='research_terminal_domain_unavailable':
                try:terminal.terminal_trace(inp)
                except ValueError as exc:require(str(exc) in PENDING_TERMINAL_ERRORS and str(exc)==rec['detail'],'terminal rejection changed')
                else:raise ValueError('pending terminal unexpectedly available')
                require(not rec['native_price_called'] and not any(o['stage']=='PRICE' and o['index']==a['window']['index']for o in e['occurrences']),'preflight pending has native occurrence')
            else:
                require(rec['reason']=='confirmed_PRICE_Infeasible','pending reason changed')
                raw=read_json(e['output']/f"window{a['window']['index']}/price/response.json")
                require(raw['native_status']=='Infeasible' and raw['has_primal'] is False and rec['occurrence']['outcome']=='verified_native_response','unconfirmed pending native outcome')
        commands,receipts=validate_quarter_receipts(commands=s['commands'],receipts=e['receipts'],cells=source['cells'][:3],window=a['window'],binding=binding,logical_clock=s['clock'])
        expected=dict(window=deepcopy(a['window']),RT_cells=deepcopy(source['cells'][:3]),DA_cells=deepcopy(inp['da_cells'][:3]),nodes=resource_nodes(inp['resources']),execution_verified=True,command_ids=[c['id']for c in commands],receipt_ids=[r['id']for r in receipts])
        require(e['completed_quarters'][j]==expected,'completed energy data not from same verified commands/receipts')
    require(success_index==len(e['price_caps']),'extra success price capabilities')
    rows=[]
    for j,q in enumerate(e['completed_quarters']):
        rec=e['price_records'][j];inp=accepted_rt_source(e['rt_caps'][j])['input']
        raw_prices={node:values[0] for node,values in rec['prices'].items()} if rec['status']=='success' else None
        quantities={}
        for uid,u in inp['resources'].items():
            def pw(c):
                return c['batteries'][uid]['power_mw'] if u['kind']=='battery' else c['thermal'][uid]['mean_mw']
            quantities[uid]=dict(simulated_executed_mwh=sum(pw(c) for c in q['RT_cells'])/12,physical_DA_reference_mwh=sum(pw(c) for c in q['DA_cells'])/12,actual_metered_mwh=None)
        vre={}
        for bus in inp['renewable']:
            def total(cells,key):return sum(c['renewable'][bus][key] for c in cells)/12
            vre[bus]=dict(simulated_dispatched_mwh=total(q['RT_cells'],'dispatched_mw'),simulated_curtailed_mwh=total(q['RT_cells'],'curtailed_mw'),synthetic_available_mwh=total(q['RT_cells'],'availability_mw'),physical_DA_reference_mwh=total(q['DA_cells'],'dispatched_mw'),financial_DA_participation='unknown',actual_metered_mwh=None)
        rows.append(dict(window=q['window'],quantities=quantities,renewable=vre,raw_unclipped_PRICE=raw_prices,raw_price_status='available_raw_only' if rec['status']=='success' else 'unpriced_pending',raw_price_scope='delivered_first_quarter_only_not_seven_lookaheads',market_price=None,settlement_price=None,command_ids=q['command_ids'],receipt_ids=q['receipt_ids'],price_substitution_applied=False))
    e['post_verification']=dict(schema=SCHEMA+'.executed_research_ledger',executed_quarters=rows,state_basis=s['plan']['seed_proof']['state_basis'],prior_rolling_RT_windows=0,replayed_DA_prefix_cells=0,initial_prefix_cells=3,market_price=None,settlement_price=None,formal_bill=None,payable=False,financial_price_available=False,actual_metered_cells=0)
    _write(e['output'],'post_verification.json',e['post_verification']);return deepcopy(e['post_verification'])



from . import gd_r46_day_models as day_models
_REFERENCES=[(subprocess,'Popen',subprocess.Popen),(subprocess.Popen,'wait',subprocess.Popen.wait),(hs,'serialize_model',hs.serialize_model)]
_REFERENCES += [(module,name,fn) for module in (rt,pricing,bridge,pure,contract,static_stage_checks,day_models,terminal,vre) for name,fn in vars(module).items() if callable(fn)]
_ORIGINALS={name:fn for name,fn in list(globals().items()) if callable(fn)}
