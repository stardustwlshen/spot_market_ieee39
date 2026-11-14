"""Causal multiwindow data runner. No devices, resume, mapping, or settlement."""
from copy import deepcopy
from datetime import timedelta
from hashlib import sha256
from pathlib import Path
import json
import os
import sys
from importlib.metadata import distributions
from . import gd_october_job_codec as codec
from . import gd_october_native_solve as native
from .gd_october_native_roll import run_october_native_roll
from .gd_october_day_inputs import validate_definitions, prepare_definition, validate_static_sources
from .gd_october_roll import (RollRequest, StateRecord, IssuedCommand, ExecutionReceipt, OctoberRollError,
    advance_state_values, fingerprint, canonical_bytes, SHANGHAI)
from .gd_october_window_job import _no_symlink, _write, _fsync_directory, _source_files, _embedded, _prepare

SCHEMA='gd.october.day.job.v1'
ARCHIVE_SCHEMA='gd.october.day.archive.v1'

def encode_day_job(*, definitions, initial_observations, initial_commands, mode, native_authorized,
                   execution_mode, issuance_source, actual_inputs):
    return codec.dumps(dict(schema=SCHEMA, definitions=definitions, initial_observations=initial_observations,
        initial_commands=initial_commands, mode=mode, native_authorized=native_authorized,
        execution_mode=execution_mode, issuance_source=issuance_source, actual_inputs=actual_inputs))

def _check(job):
    if type(job) is not dict or set(job)!={'schema','definitions','initial_observations','initial_commands','mode',
            'native_authorized','execution_mode','issuance_source','actual_inputs'} or job['schema']!=SCHEMA:
        raise ValueError('explicit day job fields required')
    if job['mode'] not in {'prepare','build','native_highs'}: raise ValueError('unknown day mode')
    if type(job['native_authorized']) is not bool or (job['mode']=='native_highs' and not job['native_authorized']):
        raise ValueError('explicit native authorization required')
    if job['execution_mode'] not in {'simulated_execution','declared_actual_input'}: raise ValueError('explicit execution mode required')
    if job['execution_mode']=='simulated_execution' and job['actual_inputs'] is not None: raise ValueError('actual records forbidden in simulation')
    if job['execution_mode']=='declared_actual_input' and type(job['actual_inputs']) is not tuple: raise ValueError('actual record tuple required')
    for k in ('initial_observations','initial_commands'):
        if type(job[k]) is not tuple: raise ValueError('immutable initial history required')
    windows=validate_definitions(job['definitions'])
    for name,cls,key in (('initial_observations',StateRecord,'state_id'),('initial_commands',IssuedCommand,'command_id')):
        history=job[name]
        if any(type(v) is not cls for v in history):raise ValueError('typed initial history required')
        for v in history:v.validate()
        if len({getattr(v,key) for v in history})!=len(history):raise ValueError('duplicate initial history identity')
    observations=job['initial_observations'];commands=job['initial_commands']
    if any(s.kind=='planned_projection' for s in observations):raise ValueError('projected initial observation forbidden')
    if len({s.as_of for s in observations})!=len(observations):raise ValueError('ambiguous initial observation boundary')
    if any(c.run_id!=windows[0].run_id for c in commands):raise ValueError('cross-run initial command')
    ordered=sorted(commands,key=lambda c:c.delivery_start)
    if any(a.delivery_end>b.delivery_start for a,b in zip(ordered,ordered[1:])):raise ValueError('overlapping initial commands')
    return windows


def run_day_job(job_path, output_dir):
    """Production rejects mock native evidence. Always creates a new archive."""
    return _run(job_path,output_dir,test_harness=False)


def run_day_test_harness(job_path, output_dir):
    """Explicit pure-test entry: mock flow allowed; NEVER native/day acceptance."""
    return _run(job_path,output_dir,test_harness=True)


def _run(job_path, output_dir, *, test_harness):
    out=Path(output_dir); _no_symlink(out); out.mkdir(parents=False,exist_ok=False); _fsync_directory(out.parent)
    report=dict(schema=ARCHIVE_SCHEMA,status='running',phase='read_job',run_id=None,day_id=None,
        expected_day_start=None,expected_day_end=None,requested_window_count=0,prepared_window_count=0,
        static_definition_validated_count=0,built_window_count=0,solved_window_count=0,completed_window_count=0,confirmed_step_count=0,
        execution_mode=None,window_records=[],native_numerical_acceptance=False,solver_called=False,
        research_thermal_assumptions=False,operational_source_verified=False,official_market_accepted=False,
        native_solve_attempted=False,mock_flow_only=False,test_harness_only=test_harness,
        full_day_completed=False,full_day_native_accepted=False,whole_day_feasibility_verified=False,
        actual_device_actions_performed=False,source_inventory_consistent=False,
        command_delivery_or_execution_authenticity_verified=False,receipt_restored=False,
        directory_fsync_supported=os.name!='nt',official_algorithm_certified=False,
        mapping_status='unavailable_no_explicit_post_result_mapping',mapping=None,
        official_price_status='unavailable',official_price=None,settlement_status='unavailable',settlement=None,
        cash=None,automatic_resume_allowed=False,failed_action_status=None)
    inventory={}
    def save(name, raw):
        p=out/name;p.parent.mkdir(parents=True,exist_ok=True);_write(p,raw)
        parent=p.parent
        while True:
            _fsync_directory(parent)
            if parent==out:break
            parent=parent.parent
        inventory[name]=dict(sha256=sha256(raw).hexdigest(),size=len(raw))
    def checkpoint(phase):
        report['phase']=phase
        save('report.pending.json',canonical_bytes(report));os.replace(out/'report.pending.json',out/'report.json')
        inventory['report.json']=inventory.pop('report.pending.json')
        files={k:v for k,v in inventory.items() if k not in {'manifest.json','manifest.pending.json'}}
        save('manifest.pending.json',canonical_bytes(dict(schema=ARCHIVE_SCHEMA,files=files)))
        os.replace(out/'manifest.pending.json',out/'manifest.json');_fsync_directory(out)
    checkpoint('read_job');before=None;roll_result=None;index=0;active={};preflights={};native_flags=[]
    try:
        _no_symlink(job_path)
        if not Path(job_path).is_file() or Path(job_path).stat().st_size>64*1024*1024: raise ValueError('regular job <=64 MiB required')
        raw=Path(job_path).read_bytes();save('job.original.json',raw);report['job_sha256']=sha256(raw).hexdigest()
        before=_source_files()
        for name,data in before.items():save('source/'+name,data)
        save('source_inventory.json',canonical_bytes([dict(path=n,sha256=sha256(b).hexdigest(),size=len(b)) for n,b in before.items()]))
        save('runtime_packages.json',canonical_bytes(sorted([dict(name=d.metadata.get('Name','unknown'),version=d.version) for d in distributions()],key=lambda d:(d['name'],d['version']))))
        report['python_version']=sys.version;checkpoint('decode')
        job=codec.loads(raw);windows=_check(job)
        from .gd_october_thermal import is_research_book
        report['research_thermal_assumptions']=any(is_research_book(d.physics.thermal_trajectories) for d in job['definitions'])
        checkpoint('validate_static_sources');validate_static_sources(job['definitions'])
        save('input_byte_inventory.json',canonical_bytes([dict(location=p,sha256=sha256(b).hexdigest(),size=len(b)) for p,b in _embedded(job)]))
        first=windows[0].delivery_start.astimezone(SHANGHAI);day_start=first.replace(hour=0,minute=0,second=0,microsecond=0)
        day_end=day_start+timedelta(days=1)
        exact_day=len(windows)==96 and first==day_start and windows[-1].delivery_start.astimezone(SHANGHAI)+timedelta(minutes=15)==day_end
        report.update(run_id=windows[0].run_id,day_id=day_start.date().isoformat(),expected_day_start=day_start.isoformat(),
            expected_day_end=day_end.isoformat(),requested_window_count=len(windows),static_definition_validated_count=len(windows),mode=job['mode'],
            execution_mode=job['execution_mode'],requested_full_day=exact_day)
        def provider(request):
            nonlocal index
            index=request.window_number;prefix=f'windows/{index:03d}/'
            checkpoint('prepare_window')
            inputs=prepare_definition(job['definitions'][index],request);_prepare(inputs,request)
            active[request.window.window_id]=inputs
            save(prefix+'request.json',codec.dumps(request));save(prefix+'inputs.json',codec.dumps(inputs))
            report['prepared_window_count']+=1
            report['window_records'].append(dict(window_number=index,window_id=request.window.window_id,
                delivery_start=request.window.delivery_start.isoformat(),path=prefix,prepared_sha256=fingerprint(inputs.prepared)))
            checkpoint('window_prepared')
            if job['mode']=='native_highs':
                checkpoint('preflight')
                context=native.preflight_october_native_window(inputs,request=request)
                preflights[request.window.window_id]=context['before']
                save(prefix+'preflight.json',canonical_bytes(context['before']))
                report['built_window_count']+=1;checkpoint('preflight_passed')
            return inputs
        event_number=0
        def event(entry):
            nonlocal event_number
            name=entry['event']
            if name=='native_solve_attempted':
                report.update(native_solve_attempted=True,solver_called=None,failed_action_status='native_outcome_unknown_do_not_retry')
            if name=='mock_solution_rejected':report.update(mock_flow_only=True,solver_called=False)
            if name=='execution_pending':report['failed_action_status']='uncertain_after_execution_call_do_not_retry'
            if name=='commands_pending':report['failed_action_status']='issuance_pending_do_not_retry'
            if name=='step_confirmed':
                report['confirmed_step_count']+=1;report['failed_action_status']='confirmed_execution_preserved_do_not_repeat'
            if name=='window_completed':report['completed_window_count']+=1
            save(f'events/{event_number:06d}.json',canonical_bytes(entry));event_number+=1;checkpoint(name)
        def selected(solution,prepared,identity):
            prefix=f'windows/{index:03d}/'
            before_identity=preflights[prepared.window.window_id]
            document=json.loads(solution.artifact)
            if any(document[k]!=before_identity[k] for k in ('input_bundle_sha256','solve_intent_sha256','physical_model_sha256')):
                raise ValueError('preflight identity changed before selected result')
            save(prefix+'selected.json',solution.artifact);save(prefix+'provenance.json',canonical_bytes(solution.provenance))
            save(prefix+'selected_identity.json',canonical_bytes(identity))
            native_flags.append(bool(solution.provenance['native_numerical_acceptance']))
            report['solved_window_count']+=1
            report['mock_flow_only'] |= bool(solution.provenance['mock_flow_only'])
            report['solver_called']=bool(solution.provenance['solver_called'])
            checkpoint('selected_archived')
        def simulation(command,previous):
            physics=active[command.window_id].prepared.physics
            values=advance_state_values(previous.values,powers_mw=command.powers_mw,states=command.states,physics=physics,
                delivery_start=command.delivery_start,delivery_end=command.delivery_end)
            state=StateRecord('simulation:'+command.command_id,command.delivery_end,command.delivery_end,command.delivery_end,
                'executed_simulation','built-in data-only simulation; no device',values)
            return ExecutionReceipt('simulation:'+command.command_id,command.command_id,fingerprint(command),fingerprint(previous),
                command.delivery_end,'built-in data-only simulation; no device',deepcopy(command.powers_mw),deepcopy(command.states),state)
        if job['mode'] in {'prepare','build'}:
            # Future executed boundaries do not yet exist. Never fabricate them.
            w=windows[0];request=RollRequest(0,w,tuple(s for s in job['initial_observations'] if s.available_at<=w.information_cutoff),
                tuple(c for c in job['initial_commands'] if c.available_at<=w.information_cutoff))
            inputs=provider(request)
            if job['mode']=='build':
                checkpoint('preflight');context=native.preflight_october_native_window(inputs,request=request)
                save('windows/000/preflight.json',canonical_bytes(context['before']));report['built_window_count']=1
            report['coverage_note']='Only first causal boundary prepared/built; later windows require confirmed rolling history.'
        else:
            checkpoint('roll_started')
            roll_result=run_october_native_roll(windows=windows,initial_observations=job['initial_observations'],initial_commands=job['initial_commands'],
                input_provider=provider,execution_mode=job['execution_mode'],issuance_source=job['issuance_source'],
                simulation_executor=simulation if job['execution_mode']=='simulated_execution' else None,
                actual_inputs=job['actual_inputs'],event_callback=event,solution_callback=selected,allow_mock_flow=test_harness)
        checkpoint('source_recheck')
        if _source_files()!=before:raise ValueError('application source changed during run')
        report['source_inventory_consistent']=True
        for name,expected in inventory.items():
            if name in {'manifest.pending.json','manifest.json'}:continue
            raw=(out/name).read_bytes()
            if expected!=dict(sha256=sha256(raw).hexdigest(),size=len(raw)):
                raise ValueError('archive bytes changed before acceptance: '+name)
        if roll_result:
            # Do not archive private live solution receipts or revive them on read.
            save('execution_report.json',canonical_bytes({k:v for k,v in roll_result.items() if k!='selected_solutions'}))
            accepted=bool(roll_result['completed']) and all(native_flags) and len(native_flags)==len(windows) and not test_harness
            report['native_numerical_acceptance']=accepted
            report['full_day_completed']=bool(exact_day and accepted and report['confirmed_step_count']==288 and report['completed_window_count']==96)
            report['full_day_native_accepted']=report['full_day_completed']
            report['all_window_models_native_accepted']=accepted
        report.update(status='completed',failed_action_status=None);checkpoint('complete')
    except Exception as exc:
        if isinstance(exc,OctoberRollError):
            roll_result=exc.report
            save('execution_report.json',canonical_bytes({k:v for k,v in roll_result.items() if k!='selected_solutions'}))
            report['failed_action_status']=('native_outcome_unknown_do_not_retry' if roll_result.get('failure_stage')=='solve_window' and report['solver_called'] is None else roll_result.get('failed_action_status'))
        report.update(status='failed',error=dict(type=type(exc).__name__,message=str(exc)),native_numerical_acceptance=False,
            full_day_completed=False,full_day_native_accepted=False,whole_day_feasibility_verified=False)
        if before is not None:
            try:report['source_inventory_consistent']=_source_files()==before
            except Exception:report['source_inventory_consistent']=False
        checkpoint(report['phase'])
    return report


def main(argv=None):
    import argparse
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('job');p.add_argument('archive');args=p.parse_args(argv)
    report=run_day_job(args.job,args.archive);print(json.dumps(report,ensure_ascii=False));return 0 if report['status']=='completed' else 1

if __name__=='__main__':raise SystemExit(main())
