"""Sequential day-start DA research runs with explicit daily bid admission.

This version resumes only at committed day boundaries. It never reruns an
uncommitted directory or overwrites a completed day. Cancellation is evaluated
between days; a process killed during a day requires a new explicit branch.
"""
from __future__ import annotations
import copy
import hashlib
import json
import os
from dataclasses import dataclass, replace
from datetime import timedelta
from pathlib import Path
import numpy as np
import pandas as pd
import yaml
from . import state_bridge as bridge
from .process_control import file_lock


@dataclass
class DayInputs:
    cfg: object
    net: object
    profiles: pd.DataFrame
    bid_data: dict | None
    bid_receipt: dict | None
    information_policy: str = 'deterministic_known_inputs_v1'
    input_source_ref: dict | None = None


class AwaitingBids(ValueError):
    pass


class UncommittedDayError(RuntimeError):
    pass


def bid_hash(bids):
    return bridge.digest(bids)


def validate_day_bids(day):
    from .bids import validate_bids, raise_on_errors
    b,r=day.bid_data,day.bid_receipt
    if not isinstance(b,dict) or not isinstance(r,dict) or r.get('confirmed') is not True:
        raise AwaitingBids(f"{day.cfg.base['date']}: daily bids are absent or unconfirmed")
    if str(b.get('date'))!=str(day.cfg.base['date']) or str(r.get('date'))!=str(day.cfg.base['date']):
        raise ValueError('Daily bid/confirmation date mismatch')
    if type(b.get('version')) is not int or b['version']<1 or type(r.get('version')) is not int or b['version']!=r['version']:
        raise ValueError('Positive daily bid version and matching confirmation required')
    if r.get('bid_hash')!=bid_hash(b): raise ValueError('Confirmed bid hash mismatch')
    if b.get('rule_set')!=day.cfg.rule_set: raise ValueError('Daily bid rule_set mismatch')
    raise_on_errors(validate_bids(day.cfg,b,require_complete=True))
    return b


def _cost_files(cfg):
    from .research_cost_setup import FILENAME
    names=('fixed_costs.yaml',FILENAME,'spot_market_costs.yaml')
    return {name:(cfg.root/'config'/name).read_text(encoding='utf-8')
            if (cfg.root/'config'/name).is_file() else None for name in names}


def day_fingerprint(day):
    return bridge.digest(dict(base=day.cfg.base,units=day.cfg.units,mode=day.cfg.mode,
        physical_identity=bridge.physical_identity(day.cfg,day.net),
        profiles=day.profiles.to_dict('list'),profile_attributes=day.profiles.attrs,
        bids=day.bid_data,receipt=day.bid_receipt,information_policy=day.information_policy,
        cost_files=_cost_files(day.cfg),input_source_ref=day.input_source_ref))


def validate_day(day):
    from .input_validation import validate_production_day,validate_time_grid,validate_physical_inputs,validate_network_inputs
    from .forecast import validate_profiles
    validate_day_bids(day)
    validate_production_day(day.cfg);validate_time_grid(day.cfg)
    if day.cfg.base.get('solver',{}).get('storage_binary',True) is not True:
        raise ValueError('A sequential actual series requires binary storage before DA admission')
    validate_physical_inputs(day.cfg);validate_network_inputs(day.cfg,day.net)
    if day.information_policy=='causal_physical_v3':
        from .series_inputs import validate_causal_day
        validate_causal_day(day)
    elif day.information_policy=='deterministic_known_inputs_v1':
        if day.input_source_ref is not None or (day.cfg.base.get('forecast') or {}).get('model')=='causal_physical_v3':
            raise ValueError('Deterministic days cannot carry a causal source or model')
        validate_profiles(day.cfg,day.profiles)
        for name in ['load']+[r['id'] for r in day.cfg.units.get('renewable',[])]:
            a=day.profiles[f'{name}_actual'].to_numpy()
            if any(not np.array_equal(a,day.profiles[f'{name}_{stage}'].to_numpy()) for stage in ('da','id','rt')):
                raise ValueError('Deterministic known-input mode requires equal actual/DA/ID/RT columns; no legacy-noise fallback')
    else:
        raise ValueError('Unknown series information policy')
    for h in day.cfg.units.get('hydro',[]):
        if h.get('commitment',{}).get('mode')!='unit_duration_v1':
            raise ValueError(f"{h['id']}: new series requires explicit identified hydro research initial state; legacy history is not migrated")
    from .spot_costs import load_profile as load_spot_costs
    load_spot_costs(day.cfg)  # Format/values only; absent unit costs remain explicitly unknown.
    return day_fingerprint(day)


def _atomic(path,value):
    content=json.dumps(bridge.plain(value),ensure_ascii=False,sort_keys=True,indent=2,allow_nan=False)
    tmp=path.with_name(path.name+f'.tmp.{os.getpid()}')
    with tmp.open('x',encoding='utf-8') as stream:
        stream.write(content);stream.flush();os.fsync(stream.fileno())
    os.replace(tmp,path)


def _record_phase(root, date, phase):
    """Small durable locator only. A SIGKILL cannot certify completion/failure."""
    from datetime import datetime, timezone
    row = dict(schema_version=1,date=date,phase=phase,
               observed_at_utc=datetime.now(timezone.utc).isoformat(),
               scope='diagnostic_only_not_day_commit')
    try:
        for line in Path('/proc/self/status').read_text().splitlines():
            if line.startswith('VmRSS:'):
                row['rss_bytes'] = int(line.split()[1])*1024
                break
    except (OSError,ValueError,IndexError):
        row['rss_bytes'] = None  # Portable optional telemetry; no fake zero.
    _atomic(root/'run_phase.json',row)


def _hash_file(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def _files(root):
    return {str(p.relative_to(root)):_hash_file(p) for p in sorted(root.rglob('*')) if p.is_file() and p.name!='day_commit.json' and 'cost_recalculations' not in p.relative_to(root).parts}


def _freeze(day,directory):
    frozen=directory/'scenario_snapshot';(frozen/'config').mkdir(parents=True)
    for name,data in (('base.yaml',day.cfg.base),('units.yaml',day.cfg.units),('modes.yaml',{day.cfg.rule_set:day.cfg.mode})):
        (frozen/'config'/name).write_text(yaml.safe_dump(bridge.plain(data),allow_unicode=True,sort_keys=False),encoding='utf-8')
    for name,text in _cost_files(day.cfg).items():
        if text is not None:(frozen/'config'/name).write_text(text,encoding='utf-8')
    _atomic(frozen/'profiles_full_precision.json',dict(columns=day.profiles.to_dict('list'),attrs=day.profiles.attrs))
    net=day.net
    network_dir=frozen/'data'/'case39';network_dir.mkdir(parents=True)
    buses=pd.DataFrame(dict(bus=net.buses,load_mw=[float(net.load_share.loc[b])*float(net.base_load_mw) for b in net.buses]))
    branches=net.branches.drop(columns=['rate_orig_mw'],errors='ignore').copy()
    factors=np.array([float(day.cfg.base.get('line_scale',{}).get(line,1.))*float(day.cfg.base.get('line_capacity_multiplier',1.)) for line in branches.branch_id])
    if not np.isfinite(factors).all() or (factors<=0).any(): raise ValueError('Invalid frozen network scale')
    branches['rate_mw']=net.branches.rate_mw.to_numpy(dtype=float)/factors
    buses.to_csv(network_dir/'buses.csv',index=False,float_format='%.17g')
    branches.to_csv(network_dir/'branches.csv',index=False,float_format='%.17g')
    _atomic(frozen/'network_export_semantics.json',dict(csv_rate='effective_rating_divided_by_frozen_line_and_global_scale',
        effective_rating='network_effective.json:branches.rate_mw',base_load='net.base_load_mw_times_frozen_bus_share',
        raw_original_file_claim=False,scale_application='exactly_once_by_existing_result_readers'))
    _atomic(frozen/'network_effective.json',dict(buses=net.buses,branches=net.branches.to_dict('records'),ptdf=net.ptdf,
        load_share={str(k):float(v) for k,v in net.load_share.items()},base_load_mw=net.base_load_mw,ref_bus=net.ref_bus))
    if day.input_source_ref is not None: _atomic(frozen/'input_source_ref.json',day.input_source_ref)
    _atomic(frozen/'bid_data.json',day.bid_data);_atomic(frozen/'bid_confirmation.json',day.bid_receipt)
    from .bids import effective_bids
    _atomic(frozen/'resolved_bids.json',effective_bids(day.cfg,day.bid_data))
    # All cost analysis reads this immutable per-day root, never the mutable source.
    return replace(copy.deepcopy(day.cfg),root=frozen)


def _records(frame):
    return [{k:(None if pd.isna(v) else v) for k,v in row.items()} for row in frame.to_dict('records')]


def _financial(result):
    from .profit import analyze
    data=analyze(result)
    from .series_financial import normalize_day
    return normalize_day(dict(date=str(result['da'].case.cfg.base['date']),summary=data['summary'],rows=_records(data['by_period'])))



def execution_evidence(result):
    """Only formal RT first segments. No window tails counted as actual."""
    keys=('P','u','Pw','Ph','Vh','Qout','Pg','Pp','xg','xp','ngen','npump','Vu','Pd','Pc','E','u_ch','u_dis')
    rows=[]
    for r in result['rr'].rt:
        sn=r['snap']
        row=dict(t=r['t'],values={k:{uid:float(a[0]) for uid,a in sn[k].items()} for k in keys},
            hydro_online={h:sum(int(a[0]) for (j,z),a in sn['delta'].items() if j==h) for h in sn['Ph']},
            hydro_units=[dict(station=h,unit_id=u,online=int(a[0])) for (h,u),a in sn.get('hydro_u',{}).items()])
        rows.append(row)
    return dict(schema_version=1,source='formal_RT_energy_SCED_first_segment_only',
        initial=bridge.pack_state(result['da'].case.cfg,result['rr'].states[0]),records=rows,
        actual_states=[bridge.pack_state(result['da'].case.cfg,s) for s in result['rr'].states],
        settlement_detail=_records(result['settle']['detail']))

def _series_financial(financial):
    from .series_financial import aggregate
    return aggregate(financial)


def _verify_commit(directory,input_hash):
    commit=json.loads((directory/'day_commit.json').read_text())
    expected={k:v for k,v in commit.items() if k!='commit_hash'}
    if commit.get('commit_hash')!=bridge.digest(expected) or commit.get('input_hash')!=input_hash:
        raise ValueError('Completed day commit/input hash mismatch; overwrite is prohibited')
    if commit['files']!=_files(directory): raise ValueError('Completed day files changed or missing')
    checkpoint=json.loads((directory/'actual_checkpoint.json').read_text())
    if commit['checkpoint_hash']!=checkpoint.get('checkpoint_hash'):
        raise ValueError('Completed day checkpoint hash mismatch')
    return checkpoint,json.loads((directory/'financial_full_precision.json').read_text())


def validate_solver_thread_budget(days):
    """HiGHS owns one global scheduler per process; do not change its size by day.

    This is an execution constraint, not a physical infeasibility. Launch a new
    worker process when selecting a different thread budget for another series.
    """
    from .input_validation import validate_solver_scheduler
    budgets=[validate_solver_scheduler(day.cfg) for day in days]
    if len(set(budgets))!=1:
        raise ValueError('One series process must use the same effective HiGHS threads for every day; change threads only in a fresh worker process')
    return budgets[0]


def run_series(days,output_dir,*,initial_actual_state,mode='all_ready',should_cancel=None,log=print,
               progress_callback=None,make_plots=False,source_bundle=None,result_retention='full_debug'):
    """All calls require an explicit first-day state; no default bids/state.

    Re-entry verifies committed days and resumes at the next date. Mid-day
    directories without a commit fail closed, never silently solve twice.
    """
    if result_retention not in ('full_debug', 'archive_only'):
        raise ValueError('result_retention must be full_debug or archive_only')
    if mode not in ('all_ready','ready_prefix'): raise ValueError('Unknown series admission mode')
    if not days: raise ValueError('At least one day is required')
    days=copy.deepcopy(list(days))
    validate_solver_thread_budget(days)
    source=None
    if source_bundle is not None:
        from .series_inputs import SeriesSource,consumer_contract
        consumer_contract()  # Fail closed before creating any run files.
        source=SeriesSource(source_bundle)
        days=source.bind(days)
    elif any(day.information_policy=='causal_physical_v3' or day.input_source_ref is not None for day in days):
        raise ValueError('Causal execution requires the explicit series-level frozen source_bundle')
    identity=bridge.physical_identity(days[0].cfg,days[0].net)
    for i,day in enumerate(days):
        if bridge.physical_identity(day.cfg,day.net)!=identity:
            raise ValueError('Cross-day technical identity/network/rules must remain fixed')
        if i and bridge.day_start(day.cfg)!=bridge.day_start(days[i-1].cfg)+timedelta(hours=24):
            raise ValueError('Series dates must be adjacent 24h days')
    initial=copy.deepcopy(initial_actual_state)
    bridge.validate_system_state(days[0].cfg,initial,expected_t=0,continuation_required=True)
    initial_hash=bridge.digest(bridge.pack_state(days[0].cfg,initial))
    # all_ready guards every bid and model input before *any* DA invocation.
    if mode=='all_ready':
        for day in days: validate_day(day)
    root=Path(output_dir);root.mkdir(parents=True,exist_ok=True)
    with file_lock(root/'.series.lock'):
        source_hash=bridge.digest({str(p.relative_to(Path(__file__).parent)):_hash_file(p)
                                  for p in sorted(Path(__file__).parent.rglob('*.py'))})
        identity_record=dict(schema_version=1,model_source_hash=source_hash,dates=[str(d.cfg.base['date']) for d in days],physical_identity=identity,
            initial_state_hash=initial_hash,timing='sequential_actual_day_end_before_next_DA_not_real_D_minus_1',
            recovery_scope='committed_day_boundaries_only',cancellation_scope='between_days_only',
            input_source=source.identity() if source is not None else None)
        identity_file=root/'series_identity.json'
        if identity_file.exists():
            if json.loads(identity_file.read_text())!=identity_record: raise ValueError('Series identity/initial state changed')
        else: _atomic(identity_file,identity_record)
        if source is not None: source=source.freeze(root)  # Fresh run-only provider, no UI preview registry.
        checkpoints=[];financial=[];new_results=[];new_result_refs=[];status='completed';stopped_date=None
        for i,day in enumerate(days):
            date=str(day.cfg.base['date'])
            try: fingerprint=validate_day(day)
            except AwaitingBids:
                if mode!='ready_prefix':raise
                status='awaiting_bids';stopped_date=date;break
            actual=initial if i==0 else bridge.next_day_state(checkpoints[-1],day.cfg,net=day.net)
            directory=root/f'day_{date}'
            if directory.exists():
                if not (directory/'day_commit.json').is_file():
                    raise UncommittedDayError(f'{date}: incomplete day exists; mid-day recovery is not validated. Preserve it and create an explicit new branch; no automatic rerun.')
                checkpoint,cash=_verify_commit(directory,fingerprint)
                if checkpoint['initial_state_hash']!=bridge.digest(bridge.pack_state(day.cfg,actual)):
                    raise ValueError('Completed day actual initial state chain mismatch')
                if checkpoint['previous_checkpoint_hash']!=(checkpoints[-1]['checkpoint_hash'] if checkpoints else None):
                    raise ValueError('Completed day predecessor hash chain mismatch')
                checkpoints.append(checkpoint);financial.append(cash);continue
            if should_cancel and should_cancel():
                status='cancelled_at_day_boundary';stopped_date=date;break
            directory.mkdir()
            failure_phase='write_admission'
            try:
                _atomic(directory/'admission.json',dict(date=date,input_hash=fingerprint,bid_hash=bid_hash(day.bid_data),
                    bid_receipt=day.bid_receipt,initial_state_hash=bridge.digest(bridge.pack_state(day.cfg,actual)),
                    information_policy=day.information_policy,input_source_ref=day.input_source_ref,status='admitted'))
                failure_phase='freeze_inputs'
                if result_retention == 'archive_only': _record_phase(root,date,failure_phase)
                frozen_cfg=_freeze(day,directory)
                failure_phase='verify_frozen_inputs'
                if result_retention == 'archive_only': _record_phase(root,date,failure_phase)
                if day_fingerprint(replace(day,cfg=frozen_cfg)) != fingerprint:
                    raise ValueError('Inputs changed while freezing; no DA admitted')
                _atomic(directory/'actual_initial_state.json',bridge.pack_state(frozen_cfg,actual))
                from .case import build_da_case
                from .full import run_full,write_full_outputs
                input_kwargs={} if source is None else dict(input_provider=source.provider,day_start_utc=bridge.at(frozen_cfg))
                failure_phase='build_DA_case'
                if result_retention == 'archive_only': _record_phase(root,date,failure_phase)
                case=build_da_case(frozen_cfg,day.net,day.profiles,day.bid_data,
                    init=bridge.state_to_case_init(actual),require_complete_bids=True,require_actual_state=True,**input_kwargs)
                if source is not None:
                    admission=json.loads((directory/'admission.json').read_text())
                    admission['DA_selected_inputs_hash']=case.selected_inputs.content_hash
                    admission['DA_forecast_id']=case.selected_inputs.forecast_id
                    _atomic(directory/'admission.json',admission)
                failure_phase='run_full'
                if result_retention == 'archive_only': _record_phase(root,date,failure_phase)
                result=run_full(frozen_cfg,case,log=log,progress_callback=progress_callback,
                    **({'retain_id_models': False, 'phase_callback': lambda name: _record_phase(root,date,name)}
                       if result_retention == 'archive_only' else {}))
                if bridge.digest(bridge.pack_state(frozen_cfg,result['rr'].states[0])) != bridge.digest(bridge.pack_state(frozen_cfg,actual)):
                    raise ValueError('Executed day initial state differs from admitted actual state')
                expected_bids=json.loads((directory/'scenario_snapshot/resolved_bids.json').read_text())
                if result['da'].case.bids != expected_bids:
                    raise ValueError('Model effective bids differ from the frozen daily bids')
                failure_phase='export_checkpoint'
                if result_retention == 'archive_only': _record_phase(root,date,failure_phase)
                checkpoint=bridge.export_checkpoint(result,input_hash=fingerprint,bid_hash=bid_hash(day.bid_data),
                    previous_hash=checkpoints[-1]['checkpoint_hash'] if checkpoints else None)
                cash=_financial(result)
                _atomic(directory/'resolved_bids.json',result['da'].case.bids)
                _atomic(directory/'actual_execution_full_precision.json',execution_evidence(result))
                failure_phase='write_daily_outputs'
                if result_retention == 'archive_only': _record_phase(root,date,failure_phase)
                write_full_outputs(result,directory,make_plots=make_plots)
                _atomic(directory/'actual_checkpoint.json',checkpoint)
                _atomic(directory/'financial_full_precision.json',cash)
                failure_phase='commit_day'
                if result_retention == 'archive_only': _record_phase(root,date,failure_phase)
                commit=dict(schema_version=1,date=date,input_hash=fingerprint,checkpoint_hash=checkpoint['checkpoint_hash'],files=_files(directory))
                _atomic(directory/'day_commit.json',dict(commit,commit_hash=bridge.digest(commit)))
            except Exception as exc:
                _atomic(root/'series_status.json',dict(status='failed_uncommitted_day',date=date,completed_days=len(checkpoints),
                    reason=str(exc),failure_phase=failure_phase,recovery_scope='day_boundaries_only_no_automatic_rerun'))
                raise
            checkpoints.append(checkpoint);financial.append(cash)
            new_result_refs.append(dict(date=date,directory=directory.name,
                checkpoint_hash=checkpoint['checkpoint_hash'],commit_hash=bridge.digest(commit)))
            if result_retention == 'full_debug':
                new_results.append(result)
            else:
                # Outputs and all independent audits completed before commit.
                # Only the shared source service and light day records survive.
                del result, case
                import gc
                gc.collect()
                _record_phase(root,date,'day_committed_models_released')
            _atomic(root/'series_status.json',dict(status='active' if i+1<len(days) else 'completed',completed_days=len(checkpoints),
                last_checkpoint_hash=checkpoint['checkpoint_hash'],total_days=len(days)))
        summary=dict(status=status,total_days=len(days),completed_days=len(checkpoints),stopped_date=stopped_date,
            timing=identity_record['timing'],recovery_scope=identity_record['recovery_scope'],
            terminal_pending_obligations=checkpoints[-1]['pending'] if checkpoints else [],
            last_checkpoint_hash=checkpoints[-1]['checkpoint_hash'] if checkpoints else None)
        _atomic(root/'series_status.json',summary)
        money=_series_financial(financial);_atomic(root/'series_financial.json',money)
        return dict(**summary,checkpoints=checkpoints,financial=money,new_results=new_results,
                    new_result_refs=new_result_refs,result_retention=result_retention)
