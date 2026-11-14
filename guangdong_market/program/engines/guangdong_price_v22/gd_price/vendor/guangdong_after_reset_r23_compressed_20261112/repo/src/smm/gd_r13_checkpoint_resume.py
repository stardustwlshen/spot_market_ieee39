"""V4 explicit research checkpoint restore, NOT restoration of live native seals.

The reviewed V3 archive is a pinned historical witness. Its 59 old workers are
independently checked again, never rerun or registered as fresh native solves.
Only immutable plan/source data, issued commands, synthetic receipts and frozen
prices are restored. All post-restore RT/price acceptances still need real native
solves. A changed archive, source manifest, plan, chain or policy fails closed.
"""
from copy import deepcopy
from dataclasses import dataclass
from datetime import timedelta
from hashlib import sha256
import importlib.util
import json
from pathlib import Path
import sys
from weakref import WeakKeyDictionary

from .gd_da_battery_candidate import digest, require, stamp
from .gd_october_job_codec import loads

V3_EVIDENCE_MANIFEST_SHA256='2862094bb24988b43854b023d9ccf124c704d485531329a1c43d4d98c30a7b09'
V3_SOURCE_MANIFEST_SHA256='fe3778ec42f378d6e2bf85549a068843c0a97ec9ccbdaf54c4dc00442172b41e'
ARCHIVED_WORKERS=59
NEXT_WINDOW=24
RESUME_CLOCK='2026-11-11T05:45:00+08:00'
_ARCHIVES=WeakKeyDictionary()
_STITCHED=WeakKeyDictionary()


class ArchivedResearchPlanPermit:
    __slots__=('__weakref__',)


@dataclass(eq=False)
class StitchedResearchLedger:
    data: dict
    fresh_ledger: object


def _read_json(path):return json.loads(Path(path).read_text())
def _sha(path):return sha256(Path(path).read_bytes()).hexdigest()


def _files(manifest):
    # The frozen source manifest is a path->SHA dictionary; the evidence
    # manifest additionally retains human-readable scope metadata.
    return manifest.get('files',manifest.get('manifest',manifest))


def verify_pinned_files(root,manifest,*,expected_count):
    root=Path(root).resolve();files=_files(manifest)
    require(type(files)is dict and len(files)==expected_count,'checkpoint file manifest count')
    for name,expected in files.items():
        relative=Path(name)
        require(not relative.is_absolute() and '..'not in relative.parts,'unsafe checkpoint relative path')
        path=root/relative
        require(path.is_file() and path.resolve().is_relative_to(root),'missing/outside checkpoint file: '+name)
        require(_sha(path)==expected,'checkpoint bytes changed: '+name)
    return files


def _audit_modules(source_root):
    """Use the exact pinned independent verifier sources, not saved pass flags."""
    directory=Path(source_root)/'reconstruction/r13_causal_audit'
    # These files are part of the pinned791source manifest. No executor or
    # environment switch, network read or solver import is performed here.
    names=('independent_checks','check_causal_artifacts')
    output=[]
    for name in names:
        spec=importlib.util.spec_from_file_location(name,directory/(name+'.py'))
        module=importlib.util.module_from_spec(spec);sys.modules[name]=module;spec.loader.exec_module(module);output.append(module)
    return tuple(output)


def _redundant_exports():
    paths=set()
    for stage,labels in (('source_replay',('day_scuc','day_fixed_sced')),('tail',('tail_scuc','tail_fixed_sced'))):
        for label in labels:
            for suffix in ('matrix','names','primal'):paths.add(f'reports/{stage}/{label}_{suffix}.json')
    for index in range(24):
        for suffix in ('matrix','names','primal'):paths.add(f'reports/windows/{index:03d}/rt_sced_{suffix}.json')
        paths.add(f'reports/windows/{index:03d}/independent_pricing/price_matrix.json')
        paths.add(f'reports/windows/{index:03d}/independent_pricing/price_model.lp')
    require(len(paths)==132,'explicit redundant archive set');return paths


def _bind_reconstructed_worker(root,index,model):
    from .highs_solver import serialize_model,validate_options
    from .gd_r13_source_recipe import mathematical_payload,compare_math
    matrix,variables,rows=serialize_model(model,validate_options(dict(threads=1,time_limit=60.,mip_rel_gap=.001,output_flag=False)))
    raw=_read_json(root/f'worker_request_{index:04d}.json');names=_read_json(root/f'worker_names_{index:04d}.json')
    fresh_names=dict(variables=[v.name for v in variables],rows=[r.name for r in rows])
    require(set(fresh_names['variables'])==set(names['variables']) and set(fresh_names['rows'])==set(names['rows']),'reconstructed historical worker name inventory differs: '+str(index))
    comparison=compare_named_mathematics(matrix,fresh_names,raw,names)
    require(comparison['passed'],'reconstructed historical worker mathematics differ: '+str(index)+' '+str(comparison.get('mismatches',[])[:2]))
    return dict(worker=index,source='pure source-DT reconstruction, no live acceptance',matrix_bound=True,names_bound=True,comparison=comparison)


def compare_named_mathematics(left,left_names,right,right_names):
    """Compare every named column and full row multiset despite JSON/set order.

    ConstraintList insertion and line dictionary order are not physical meaning.
    No coefficient is rounded or repaired. Every original coefficient and bound
    is compared at1e-9; missing numerical-zero terms are explicitly zero-filled.
    """
    from collections import defaultdict
    from .gd_r13_source_recipe import compare_math
    def named(m,n):
        require(len(n['variables'])==len(m['variables'])==len(set(n['variables'])),'historical column name length/uniqueness mismatch')
        require(len(n['rows'])==len(m['rows'])==len(set(n['rows'])),'historical row name length/uniqueness mismatch')
        def terms(xs):
            d={}
            for index,value in xs:
                name=n['variables'][index];d[name]=d.get(name,0.)+value
            return d
        groups=defaultdict(list)
        for row in m['rows']:
            v=terms(row['terms']);key=(row['lb']is not None,row['ub']is not None,tuple(sorted(k for k,x in v.items()if abs(x)>1e-9)))
            groups[key].append(dict(lb=row['lb'],ub=row['ub'],terms=v))
        return dict(variables=dict(zip(n['variables'],m['variables'])),objective=dict(constant=m['objective']['constant'],minimize=m['objective']['minimize'],terms=terms(m['objective']['terms'])),groups=groups)
    a,b=named(left,left_names),named(right,right_names)
    require(set(a['groups'])==set(b['groups']),'historical equation sparsity families changed')
    rows=[]
    def order(row):return (float('-inf')if row['lb']is None else row['lb'],float('inf')if row['ub']is None else row['ub'],tuple(sorted(row['terms'].items())))
    for key in sorted(a['groups'],key=repr):
        aa=sorted(a['groups'][key],key=order);bb=sorted(b['groups'][key],key=order)
        require(len(aa)==len(bb),'historical equation multiplicity changed')
        for x,y in zip(aa,bb):
            union=set(x['terms'])|set(y['terms'])
            x['terms']={k:x['terms'].get(k,0.)for k in union};y['terms']={k:y['terms'].get(k,0.)for k in union}
            rows.append((x,y))
    aa=dict(variables=a['variables'],objective=a['objective'],rows=[x for x,y in rows]);bb=dict(variables=b['variables'],objective=b['objective'],rows=[y for x,y in rows])
    proof=compare_math(aa,bb,tolerance=1e-9);proof['comparison_basis']='named columns and full mathematical row multiset; no rounding';return proof


def _bind_archived_rt_primal(root,index,report):
    response=_read_json(root/f'worker_response_{index:04d}.json');names=_read_json(root/f'worker_names_{index:04d}.json')
    values=dict(zip(names['variables'],response['primal']))
    for j,cell in enumerate(report['physical_cells']):
        for g,row in cell['thermal'].items():
            require(abs(values[f'power[{g},{j}]']-row['mean_mw'])<=1e-7,'report/worker thermal primal mismatch')
        for u,row in cell['batteries'].items():
            for variable,field in (('charge','charge_mw'),('discharge','discharge_mw'),('energy','energy_end_mwh')):
                require(abs(values[f'battery.{variable}[{u},{j}]']-row[field])<=1e-7,'report/worker battery primal mismatch')
    return True


def _reconstruct_sources(base):
    from .gd_da_battery_ieee39 import load_prepared_ieee39_battery_da
    from .gd_r13_da_publication import prepare_tail,_decorate
    day=load_prepared_ieee39_battery_da((base/'source_replay/source_reconstructed_day_inputs.json').read_bytes())
    dr=_read_json(base/'source_replay/accepted_day_research_report.json')
    tr=_read_json(base/'tail/accepted_tail_report.json')
    initial=loads((base/'source_replay/issued_day_research_plan.json').read_bytes())
    plan=loads((base/'tail/published_extended_plan.json').read_bytes())
    # Original generated_at is not present in the tail archive. Reconstruct
    # mathematical inputs deterministically, explicitly not its old input hash.
    tail=prepare_tail(None,day,dr,generated_at_utc=day['contract']['generated_at_utc'])
    require({k:s['sha256']for k,s in tail['contract']['sources'].items()}==tr['source_sha256'],'reconstructed tail byte sources differ')
    require(plan['predecessor_plan_sha256']==digest(initial) and plan['supersedes_plan_id']==initial['plan_id'],'checkpoint publication lineage')
    require(plan['physical_cells'][:288]==initial['physical_cells'],'tail mutated earlier day publication')
    require(initial['day_report_sha256']==plan['day_report_sha256']==digest(dr) and plan['tail_report_sha256']==digest(tr),'archived report/publication hash mismatch')
    day_cells,day_events=_decorate(dr,day);tail_cells,tail_events=_decorate(tr,tail)
    require(day_cells==initial['physical_cells'] and day_events==initial['events'],'published day differs from source-based accepted report')
    for cell,published in zip(tail_cells,plan['physical_cells'][288:]):
        expected=deepcopy(cell);expected['plan_version_available_at']=plan['published_at']
        for row in expected['batteries'].values():row['notice_available_at']=plan['published_at']
        require(expected==published,'tail publication differs from reconstructed source report')
    require(stamp(initial['published_at'])==stamp('2026-11-10T20:30:00+08:00') and stamp(plan['published_at'])==stamp('2026-11-11T00:45:00+08:00'),'original publication clocks changed')
    require(initial['selected_paths']==plan['selected_paths']==dr['selected_paths'] and plan['tail_selected_paths']==tr['selected_paths'],'archived thermal selection changed')
    return day,tail,initial,plan,dr,tr


def restore_research_checkpoint(*,archive_root,source_root,evidence_manifest,source_manifest,expected_evidence_manifest_sha256=V3_EVIDENCE_MANIFEST_SHA256,expected_source_manifest_sha256=V3_SOURCE_MANIFEST_SHA256):
    """Full read-only re-audit then mint a distinct archived-source capability.

    Constants pin the coordinator-reviewed V3 attempt. Merely supplying a new
    manifest, caller flag or modified report cannot authorize another archive.
    This API runs zero optimizers and never touches old acceptance registries.
    """
    require(expected_evidence_manifest_sha256==V3_EVIDENCE_MANIFEST_SHA256 and _sha(evidence_manifest)==V3_EVIDENCE_MANIFEST_SHA256,'unapproved evidence manifest')
    require(expected_source_manifest_sha256==V3_SOURCE_MANIFEST_SHA256 and _sha(source_manifest)==V3_SOURCE_MANIFEST_SHA256,'unapproved V3source manifest')
    root=Path(archive_root).resolve();base=root/'reports';original=Path(source_root).resolve()
    manifest=_read_json(evidence_manifest);source_doc=_read_json(source_manifest)
    all_evidence_files=_files(manifest);require(len(all_evidence_files)==606,'original evidence manifest count')
    redundant=_redundant_exports();require(redundant<=set(all_evidence_files),'original canonical export inventory changed')
    canonical_files={k:v for k,v in all_evidence_files.items()if k not in redundant}
    evidence_files=verify_pinned_files(root,canonical_files,expected_count=474)
    source_files=verify_pinned_files(original,source_doc,expected_count=791)
    outcome=_read_json(root/'outcome.json');identity=_read_json(root/'observer_identity.json')
    require(outcome['observed_worker_events']==ARCHIVED_WORKERS and outcome['source_unchanged'] and not outcome['callable_replacements'] and not outcome['retry'],'archived observer provenance or count changed')
    require(identity['manifest_sha256']==V3_SOURCE_MANIFEST_SHA256 and identity['observer_sha256']==_sha(root/'observer_source.py'),'observer/source identity changed')
    require(_read_json(root/'source_before.json')==_read_json(root/'source_after.json'),'V3source changed during archived run')
    request_files=sorted(root.glob('worker_request_????.json'))
    require([p.name for p in request_files]==[f'worker_request_{i:04d}.json'for i in range(1,60)],'missing or extra archived native worker')
    audit,causal=_audit_modules(original)
    proofs=[]
    for index in range(1,60):
        matrix=_read_json(root/f'worker_request_{index:04d}.json');response=_read_json(root/f'worker_response_{index:04d}.json')
        proofs.append(dict(worker=index,**audit.audit_worker_pair(matrix,response)))
    bindings=[]
    reports=[];inputs={};prices={}
    for index in range(24):
        folder=base/'windows'/f'{index:03d}';worker=10+index*2 if index<4 else 20+(index-4)*2
        report=_read_json(folder/'accepted_rt_report.json');reports.append(report)
        inputs[report['start']]=loads((folder/'inputs.json').read_bytes());prices[report['start']]=_read_json(folder/'independent_pricing/price_result.json')
        from .gd_r13_causal_rt import _build_rt_physics
        from .gd_horizon import EnergyHorizon,EnergyPeriod
        construction=deepcopy(inputs[report['start']]);construction['energy_inputs']['horizon']=EnergyHorizon(tuple(EnergyPeriod(t,1/12,1/12,'delivery',t<3)for t in range(24)),'real_time_5min','V4 historical mathematics recheck, no solve')
        rm=_build_rt_physics(construction);bindings.append(_bind_reconstructed_worker(root,worker,rm));_bind_archived_rt_primal(root,worker,report)
        from .gd_r13_pricing import build_pricing_lp
        price_input=_read_json(folder/'independent_pricing/price_inputs.json');price_input['source_kind']='diagnostic_fixture'
        pm=build_pricing_lp(price_input);bindings.append(_bind_reconstructed_worker(root,worker+1,pm.model))
        values=_read_json(folder/'independent_pricing/price_primal_dual.json')['primal'];price_names=_read_json(root/f'worker_names_{worker+1:04d}.json')
        require([v['name']for v in values]==price_names['variables'] and [v['value']for v in values]==_read_json(root/f'worker_response_{worker+1:04d}.json')['primal'],'archived price primal/worker identity')
        del rm,pm
        require(abs(report['metrics']['objective_yuan']-proofs[worker-1]['objective'])<=audit.cost_tolerance(proofs[worker-1]['objective']),'RT report/native worker objective identity')
    day,tail,initial,plan,day_report,tail_report=_reconstruct_sources(base)
    audit.audit_sources(day['contract']);audit.audit_da_physics(day,day_report)
    # validate_boundary_report reconstructs all seven preparation mathematical
    # certificates against their source recipe without granting a live permit.
    from .gd_da_prepared_boundary import validate_boundary_report
    validate_boundary_report(day['prepared_boundary'])
    history=_read_json(base/'windows/023/chain_after_advance.json');failure=_read_json(base/'failure.json')
    require(failure['chain']==history and failure['stage']=='RT_window_024' and failure['native_failure']is None and failure['accepted_windows']==24,'archive stop point changed')
    ledger=_read_json(base/'partial_energy_ledger.json');predeclared=_read_json(base/'predeclared.json')
    policy=_read_json(original/'reconstruction/r13_price_audit/predeclared_ieee_rt_pricing_policy_v1.json')
    require(predeclared['pricing_policy']==policy,'archived pricing policy changed')
    for value in inputs.values():
        require(value['scope']['uses_future_actual']is False,'future actual archive source')
    chain_proof=causal.audit_chain(history,day,[initial,plan],reports,inputs)
    ledger_proof=causal.audit_energy_ledger(ledger,history,reports,inputs,prices)
    require(history['clock']==RESUME_CLOCK and len(history['accepted_windows'])==24,'exact checkpoint clock/windows required')
    require(chain_proof['issued_RT_cells']==72 and chain_proof['executed_RT_cells']==69,'issued/executed checkpoint counts')
    pending=[c for c in history['commands']if c['id']not in {r['command_id']for r in history['receipts']}]
    require(len(pending)==3 and [c['cell']['start']for c in pending]==[(stamp(RESUME_CLOCK)+timedelta(minutes=5*i)).isoformat()for i in range(3)],'exact three pending commands required')
    require(len(ledger['frozen_prices'])==24 and len(ledger['executed_quarters'])==23,'price/execution checkpoint mismatch')
    proof=dict(schema='gd.research.r13.v4.checkpoint_restore_proof.v1',evidence_manifest_sha256=V3_EVIDENCE_MANIFEST_SHA256,
        source_manifest_sha256=V3_SOURCE_MANIFEST_SHA256,source_files=791,evidence_files=474,original_manifest_files=606,redundant_exports_omitted=132,historical_native_workers=59,
        new_native_workers=0,old_native_acceptance_restored=False,scope='verified historical native evidence; synthetic state and planned data only',
        next_window=24,clock=history['clock'],pending_command_ids=[c['id']for c in pending],plan_sha256=digest(plan),history_sha256=digest(history),ledger_sha256=digest(ledger),policy_sha256=digest(policy),
        worker_proofs=proofs,worker_export_bindings=bindings,chain_proof=chain_proof,ledger_proof=ledger_proof)
    payload=dict(day=day,tail=tail,plan=plan,initial_plan=initial,day_report=day_report,tail_report=tail_report,history=history,ledger=ledger,policy=policy,
        reports={RESUME_CLOCK:reports[-1]},window_inputs={RESUME_CLOCK:inputs[RESUME_CLOCK]},prices={RESUME_CLOCK:prices[RESUME_CLOCK]},proof=proof)
    permit=ArchivedResearchPlanPermit();_ARCHIVES[permit]=dict(payload=deepcopy(payload),seal=digest(payload),root=root,source_root=original,
        evidence_manifest_path=Path(evidence_manifest),source_manifest_path=Path(source_manifest))
    return dict(permit=permit,plan=deepcopy(plan),proof=deepcopy(proof),policy=deepcopy(policy))


def _archive(permit):
    require(type(permit)is ArchivedResearchPlanPermit and permit in _ARCHIVES,'reviewed archive research capability required')
    r=_ARCHIVES[permit];require(digest(r['payload'])==r['seal'],'restored archive payload modified');return r['payload']


def archived_plan_sources(plan,permit):
    p=_archive(permit);require(digest(plan)==digest(p['plan']),'archived plan/source changed')
    return deepcopy(p['day']),deepcopy(p['tail'])


def checkpoint_proof(permit):return deepcopy(_archive(permit)['proof'])


def restore_simulated_chain(permit):
    from .gd_r13_causal_rt import SimulatedChain,_CHAINS
    p=_archive(permit);bundle=deepcopy(p['day'])
    for u,r in bundle['batteries'].items():
        for date,budget in p['tail']['batteries'][u]['cycle_budget'].items():
            if date not in r['cycle_budget']:r['cycle_budget'][date]=dict(limit_cycles=budget['limit_cycles'],used_internal_mwh=0.)
    history=deepcopy(p['history']);chain=SimulatedChain(history)
    _CHAINS[chain]=dict(seal=digest(history),bundle=bundle,plan=deepcopy(p['plan']),permit=permit,
        day_source_sha256=digest(p['day']),archive_resume_provenance=deepcopy(p['proof']))
    return chain


def new_stitched_ledger(permit):
    from .gd_r13_energy_ledger import new_energy_ledger
    p=_archive(permit);data=dict(schema='gd.research.r13.v4.stitched_energy_ledger.v1',archived=deepcopy(p['ledger']),pending_record=None,
        provenance=dict(historical_native_workers=59,old_recorded_quarters=23,old_frozen_quarters=24,old_pending_quarter='2026-11-11T05:45:00+08:00',
            evidence_manifest_sha256=V3_EVIDENCE_MANIFEST_SHA256,fresh_native_starts_at_window=24,old_native_seals_restored=False))
    ledger=StitchedResearchLedger(data,new_energy_ledger());_STITCHED[ledger]=dict(permit=permit,seal=digest(data),fresh_ledger=ledger.fresh_ledger);return ledger


def _stitched(ledger):
    require(type(ledger)is StitchedResearchLedger and ledger in _STITCHED,'stitched research ledger required')
    r=_STITCHED[ledger];require(ledger.fresh_ledger is r['fresh_ledger'],'stitched fresh ledger identity changed');require(digest(ledger.data)==r['seal'],'stitched archived ledger modified');_archive(r['permit']);return r


def record_available_stitched_quarters(ledger,chain):
    from .gd_r13_causal_rt import chain_report
    from .gd_r13_energy_ledger import record_available_quarters,quarter_energy_rows
    binding=_stitched(ledger);p=_archive(binding['permit']);h=chain_report(chain);start=RESUME_CLOCK;end=stamp(start)+timedelta(minutes=15)
    archived=p['ledger'];frozen=archived['frozen_prices'][start]
    require(start not in archived['executed_quarters'],'pending archived quarter already recorded')
    if ledger.data['pending_record']is None and stamp(h['clock'])>=end:
        report=p['reports'][start];source=p['window_inputs'][start]
        require(digest(report)==frozen['rt_report_sha256'],'pending archived frozen source changed')
        commands=[c for c in h['commands']if c['native_source_sha256']==frozen['rt_report_sha256']]
        require(commands==[c for c in p['history']['commands']if c['native_source_sha256']==frozen['rt_report_sha256']] and len(commands)==3,'pending issued commands changed')
        receipts=[]
        for c in commands:
            rs=[r for r in h['receipts']if r['command_id']==c['id'] and r.get('command_sha256')==digest(c)]
            require(len(rs)==1 and stamp(rs[0]['at'])==stamp(c['cell']['end'])<=stamp(h['clock']) and stamp(rs[0]['available_at'])<=stamp(h['clock']),'pending quarter receipts absent or premature')
            require(not rs[0]['is_measurement'] and rs[0]['kind']=='synthetic_zero_tracking_error_receipt','pending quarter promoted to actual')
            receipts.append(rs[0])
        nodes=dict(thermal={g:d['bus']for g,d in source['energy_inputs']['thermal'].items()},battery={u:d['bus']for u,d in source['batteries'].items()})
        rows=quarter_energy_rows([c['cell']for c in commands],source['da_cells'][:3],nodes,frozen['prices_yuan_per_mwh'])
        ledger.data['pending_record']=dict(start=start,end=end.isoformat(),recorded_at=h['clock'],status='simulated_RT_deviation_energy_diagnostic',price_model_sha256=frozen['price_model_sha256'],receipt_ids=[r['id']for r in receipts],command_ids=[c['id']for c in commands],rows=rows,actual_metered_energy_mwh=None,official_settlement_yuan=None,lookahead_energy_counted=False,
            source_acceptance='verified historical V3 price and dispatch, not fresh native acceptance',evidence_manifest_sha256=V3_EVIDENCE_MANIFEST_SHA256)
        binding['seal']=digest(ledger.data)
    return record_available_quarters(ledger.fresh_ledger,chain)


def stitched_energy_ledger_report(ledger):
    from .gd_r13_energy_ledger import energy_ledger_report,hourly_component,derive_time_coverage
    _stitched(ledger);fresh=energy_ledger_report(ledger.fresh_ledger);old=ledger.data['archived'];result=deepcopy(old)
    require(not(set(old['frozen_prices'])&set(fresh['frozen_prices'])),'duplicate archived/fresh frozen price')
    require(not(set(old['executed_quarters'])&set(fresh['executed_quarters'])),'duplicate archived/fresh energy quarter')
    result['frozen_prices'].update(fresh['frozen_prices']);result['executed_quarters'].update(fresh['executed_quarters'])
    if ledger.data['pending_record']is not None:result['executed_quarters'][RESUME_CLOCK]=deepcopy(ledger.data['pending_record'])
    quarters=result['executed_quarters'];by_hour={}
    for start,q in quarters.items():by_hour.setdefault(stamp(start).replace(minute=0,second=0,microsecond=0).isoformat(),[]).append(q)
    result['hourly_components']=[hourly_component(sorted(qs,key=lambda x:stamp(x['start'])))for hour,qs in sorted(by_hour.items())if len(qs)==4]
    day=stamp('2026-11-11T00:00:00+08:00')
    result.update(derive_time_coverage(result,day.isoformat()));result['coverage']=dict(whole_day_simulated=result['whole_day'],executed_quarters=len(quarters),frozen_quarter_prices=len(result['frozen_prices']),complete_hours=len(result['hourly_components']),actual_execution=False,official_settlement=False)
    result['resume_provenance']=deepcopy(ledger.data['provenance']);return result
