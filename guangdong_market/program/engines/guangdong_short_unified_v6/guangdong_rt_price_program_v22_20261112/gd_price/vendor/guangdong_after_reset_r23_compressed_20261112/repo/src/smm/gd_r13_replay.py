"""Explicit native R13 source-only replay, retained full math and numerical proof."""
from pathlib import Path
from datetime import datetime, timezone
import json
import pyomo.environ as pyo
from .gd_da_candidate import require,digest
from .gd_october_job_codec import dumps
from .gd_da_prepared_boundary import solve_prepared_boundary_live,validate_live_boundary
from .gd_da_battery_ieee39 import build_ieee39_battery_da,portable_bundle
from .gd_da_battery_candidate import solve_scuc,solve_fixed_selection_sced,accepted_da_report
from .highs_solver import serialize_model,validate_options
from .gd_r13_source_recipe import reconstruct_boundary,reconstruct_day,source_cells,TAIL_POLICY,compare_math,mathematical_payload,independent_primal_check
from .gd_r13_da_publication import prepare_tail,build_tail,publish_research_plan,extend_research_plan


def write(path,value):
    Path(path).write_text(json.dumps(value,sort_keys=True,indent=2,allow_nan=False))


def _export(model,output,label,options,*,with_values):
    matrix,variables,rows=serialize_model(model,options)
    write(output/(label+'_matrix.json'),matrix)
    write(output/(label+'_names.json'),dict(variables=[v.name for v in variables],rows=[r.name for r in rows]))
    if with_values:
        primal=[pyo.value(v) for v in variables]
        write(output/(label+'_primal.json'),dict(variables=[dict(name=v.name,value=x,fixed=v.fixed,integer=v.is_integer()) for v,x in zip(variables,primal)],objective_yuan=pyo.value(model.total_cost)))
        proof=independent_primal_check(matrix,primal);write(output/(label+'_independent_primal.json'),proof)
        require(proof['passed'],'independent mathematical primal verification failed')
    return matrix


def _compare_reference(output,reference,label,index,model,matrix):
    reference=Path(reference)
    old=json.loads((reference/f'worker_request_{index:03d}.json').read_text())
    if 'payload' in old:old=old['payload']
    proof=compare_math(mathematical_payload(matrix),mathematical_payload(old))
    names=[v.name for v in model.component_data_objects(pyo.Var,descend_into=True)]
    old_primal=json.loads((reference/f'dispatch_primal_{index:03d}.json').read_text())
    old_by_name={x['name']:x['value'] for x in old_primal['variables']}
    values={v.name:pyo.value(v) for v in model.component_data_objects(pyo.Var,descend_into=True)}
    primal_comparison=compare_math(values,old_by_name,tolerance=1e-6)
    # Independently evaluate the old witness against fresh equations as well.
    old_cross=independent_primal_check(matrix,[old_by_name[n] for n in names])
    result=dict(matrix_comparison=proof,primal_comparison=primal_comparison,old_primal_in_fresh_model=old_cross,
        objective_comparison=compare_math(pyo.value(model.total_cost),old_primal['objective_yuan'],tolerance=1e-6),
        runtime_metadata_bit_exact_claim=False,excluded_generated_input_read=False)
    write(output/(label+'_R12_independent_comparison.json'),result)
    require(proof['passed'],'R12 mathematical matrix changed')
    require(old_cross['passed'],'R12 primal no longer satisfies reconstructed model')
    return result


def run_source_replay(root, output, *, authorize_native=False, time_limit_s=180., boundary_time_limit_s=60., tail_time_limit_s=90.,
        reference_r12=None, include_tail=False, after_day=None, extension_publication_at=None):
    """Caller owns serialized native lane and resource limits. Returns live handles.

    after_day receives accepted live day objects before optional tail work. No
    import, report load or digest can recreate those accepted model handles.
    """
    require(authorize_native is True,'explicit coordinator native release required')
    require(0<time_limit_s<=900 and 0<boundary_time_limit_s<=120 and 0<tail_time_limit_s<=180,'bounded native limits')
    require(not include_tail or extension_publication_at is not None,'prospective tail requires explicit extension_publication_at')
    output=Path(output);require(not output.exists(),'new evidence directory required');output.mkdir(parents=True)
    root=Path(root);options=validate_options(dict(threads=1,time_limit=time_limit_s,mip_rel_gap=.001,output_flag=False))
    source=source_cells(root)
    write(output/'predeclared_recipe.json',dict(schema='gd.research.r13.native_recipe.v1',declared_at_utc=datetime.now(timezone.utc).isoformat(),
        tail_policy=TAIL_POLICY,seed=20261111,static_sha256=source['static_sha256'],source_availability='simulated D-1 19:30',cutoff=source['cutoff'],publication=source['publication_at'],
        source_only=True,generated_R12_inputs_required=False,solver=dict(backend='highs',threads=1,mip_rel_gap=.001),official_DA98=False))
    write(output/'source_cells_309.json',source)
    stage='preparation'
    try:
        problem=reconstruct_boundary(root);(output/'boundary_problem.json').write_bytes(dumps(problem))
        boundary,boundary_permit=solve_prepared_boundary_live(problem,time_limit_s=boundary_time_limit_s,threads=1)
        validate_live_boundary(boundary,boundary_permit);(output/'fresh_boundary_report.json').write_bytes(dumps(boundary))
        bundle=reconstruct_day(root,boundary);(output/'source_reconstructed_day_inputs.json').write_bytes(dumps(portable_bundle(bundle)))
        model=build_ieee39_battery_da(bundle,boundary_permit=boundary_permit)
        _export(model,output,'day_scuc',options,with_values=False)
        stage='day_scuc';scuc=solve_scuc(model,time_limit_s=time_limit_s,threads=1)
        write(output/'day_scuc_native_output.json',scuc.metrics)
        scuc_matrix=_export(model,output,'day_scuc',options,with_values=True)
        if reference_r12:_compare_reference(output,reference_r12,'day_scuc',8,model,scuc_matrix)
        del scuc_matrix
        stage='day_fixed_sced';sced=solve_fixed_selection_sced(model,time_limit_s=time_limit_s,threads=1)
        write(output/'day_fixed_sced_native_output.json',sced.metrics)
        sced_matrix=_export(sced.model,output,'day_fixed_sced',options,with_values=True)
        if reference_r12:_compare_reference(output,reference_r12,'day_fixed_sced',9,sced.model,sced_matrix)
        del sced_matrix
        report=accepted_da_report(sced);write(output/'accepted_day_research_report.json',report)
        day_plan,day_permit=publish_research_plan(sced,bundle,boundary_permit=boundary_permit)
        (output/'issued_day_research_plan.json').write_bytes(dumps(day_plan))
        result=dict(day_solution=sced,day_scuc_solution=scuc,day_model=model,day_bundle=bundle,boundary_permit=boundary_permit,plan=day_plan,plan_permit=day_permit,tail_solution=None,tail_bundle=None)
        if after_day:after_day(result)
        if include_tail:
            stage='tail_construct';tail_bundle=prepare_tail(root,bundle,report)
            portable=deepcopy_without_horizon(tail_bundle);(output/'source_reconstructed_tail_inputs.json').write_bytes(dumps(portable))
            tail_model=build_tail(sced,bundle,tail_bundle)
            _export(tail_model,output,'tail_scuc',options,with_values=False)
            stage='tail_scuc';tail_uc=solve_scuc(tail_model,time_limit_s=tail_time_limit_s,threads=1)
            write(output/'tail_scuc_native_output.json',tail_uc.metrics);_export(tail_model,output,'tail_scuc',options,with_values=True)
            stage='tail_fixed_sced';tail_solution=solve_fixed_selection_sced(tail_model,time_limit_s=tail_time_limit_s,threads=1)
            write(output/'tail_fixed_sced_native_output.json',tail_solution.metrics);_export(tail_solution.model,output,'tail_fixed_sced',options,with_values=True)
            tail_report=accepted_da_report(tail_solution);write(output/'accepted_tail_research_report.json',tail_report)
            plan,permit=extend_research_plan(day_plan,day_permit,tail_solution,tail_bundle,extension_publication_at=extension_publication_at)
            (output/'issued_full_research_plan.json').write_bytes(dumps(plan))
            result.update(tail_solution=tail_solution,tail_bundle=tail_bundle,tail_model=tail_model,tail_scuc_solution=tail_uc,plan=plan,plan_permit=permit)
        write(output/'outcome.json',dict(accepted_source_reconstructed_day=True,accepted_tail=include_tail,accepted_research_publication=True,
            actual_completed_at_utc=datetime.now(timezone.utc).isoformat(),official_DA98=False,execution_authority=False,excluded_generated_input_read=False))
        return result
    except Exception as exc:
        failure=getattr(exc,'failure_record',None)
        write(output/'failure.json',dict(stage=stage,type=type(exc).__name__,message=str(exc),native_failure=failure,
            actual_failed_at_utc=datetime.now(timezone.utc).isoformat(),no_input_repair=True,official_DA98=False,execution_authority=False))
        raise


def deepcopy_without_horizon(bundle):
    from copy import deepcopy
    p=deepcopy(bundle);p['energy_inputs']['horizon']=dict(factory='smm.gd_da_battery_candidate.local_diagnostic_horizon',physical_count=len(bundle['energy_inputs']['horizon'].ids))
    return p
