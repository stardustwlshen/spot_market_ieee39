"""Read-only 18-combination coverage and optional formal-model validation.

Writes a new evidence directory, never rewrites the source scene or quotes.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'))
import numpy as np
import pandas as pd
from smm.config import load_config, ROOT
from smm.typical_scenarios import prepare_typical, fingerprint
from smm.scenario_assessment import combination_matrix
from smm.case import build_da_case
from smm.network import build_network


def strict_relaxation(case):
    """LP infeasibility proves no zero-slack integer schedule; LP success does not."""
    import pyomo.environ as pyo
    from smm import scuc, regulation
    m = scuc.build_model(case)
    regulation.prepare_energy(m)
    for name in ('shed', 'surplus', 'sl', 'sR_up', 'sR_dn'):
        for variable in getattr(m, name).values(): variable.fix(0.)
    pyo.TransformationFactory('core.relax_integer_vars').apply_to(m)
    m.obj.deactivate()
    m.feasibility_objective = pyo.Objective(expr=0.)
    opt = scuc._solver(case.cfg, mip=False)
    res = opt.solve(m, load_solutions=False)
    termination = str(res.solver.termination_condition)
    return dict(termination=termination, zero_slack_lp_relaxation=termination == 'optimal',
                proves_strict_integer_infeasible=termination == 'infeasible',
                scope='LP_necessary_condition_only;all_integer_variables_relaxed;not_executable_dispatch')


def solve_dispatch(case, directory, full=False):
    from smm.da import run_da, summarize
    if full:
        from smm.full import run_full, write_full_outputs
        result = run_full(case.cfg, case, periods=case.cfg.T, retain_id_models=False)
        summary = write_full_outputs(result, directory, make_plots=False)
        return dict(summary=summary, da_scuc_info=result['da'].scuc_info,
                    da_sced_info=result['da'].sced_info, full_day_rt_checked=bool(len(result['rr'].rt)==case.cfg.T and np.isclose(case.cfg.T*case.cfg.dt,24.)),
                    rt_periods=len(result['rr'].rt), checks=result['checks'],
                    scope='formal_DA_and_full_configured_RT_horizon_and_settlement;see_per_check_scope')
    da = run_da(case, verbose=True)
    from smm.pipeline import write_outputs
    summary = write_outputs(da, directory, make_plots=False)
    return dict(da=summary, da_scuc_info=da.scuc_info, da_sced_info=da.sced_info,
                full_day_rt_checked=False, scope='DA_only;not_full_day_operational_acceptance')


def audit(root, output, solve='none', selected=None, time_limit=None, split_renewables=False, wind_event='none'):
    output = Path(output); output.mkdir(parents=True, exist_ok=False)
    before = fingerprint(root)
    cfg = load_config(root=root)
    from smm import scenario
    import shutil
    scenario._copy_tree(Path(root), output/'source_inputs')
    if (Path(root)/'current_quote.json').exists():
        shutil.copy2(Path(root)/'current_quote.json', output/'source_inputs/current_quote.json')
    matrix = combination_matrix(split_renewables)
    if selected:
        unknown = set(selected) - {row['id'] for row in matrix}
        if unknown: raise ValueError(f'Unknown combination IDs: {sorted(unknown)}')
        matrix = [row for row in matrix if row['id'] in selected]
    manifest = dict(schema=1, source_root=str(Path(root).resolve()), source_fingerprint=before,
                    tie_line_mode=cfg.base.get('tie_line_mode', 'supply'), solve=solve,
                    forecast_model=cfg.base.get('forecast', {}).get('model', 'legacy_v1'),
                    time_limit_override_s=time_limit, expected_count=len(matrix), completed_count=0,
                    coverage=('2_water_x_3_wind_x_3_pv_x_3_load' if split_renewables else '2_water_x_3_joint_renewable_x_3_load'),
                    wind_event=wind_event,
                    interpretation='synthetic_factorial_sensitivity_not_statistical_typical_days', combinations=matrix)
    rows = []
    for cell in matrix:
        start = time.perf_counter()
        target = output/cell['id']; target.mkdir()
        preview = prepare_typical(root, season=cell['season'], renewable_multiplier=cell['renewable_multiplier'],
                                  load_multiplier=cell['load_multiplier'], wind_multiplier=cell.get('wind_multiplier'),
                                  pv_multiplier=cell.get('pv_multiplier'), wind_event=wind_event)
        preview['profiles'].to_csv(target/'profiles.csv', index=False)
        (target/'assessment.json').write_text(json.dumps(preview['assessment'], ensure_ascii=False, indent=2), encoding='utf-8')
        target_cfg = load_config(root=root, season=cell['season'])
        target_cfg.base['load_scale'] = preview['load_scale']
        if time_limit is not None: target_cfg.base['solver']['time_limit_s'] = float(time_limit)
        import yaml
        (target/'effective_base.yaml').write_text(yaml.safe_dump(target_cfg.base, allow_unicode=True), encoding='utf-8')
        (target/'effective_bids.json').write_text(json.dumps(preview['bids'], ensure_ascii=False, indent=2), encoding='utf-8')
        case = build_da_case(target_cfg, build_network(target_cfg), preview['profiles'], preview['bids'])
        solved = {}
        try:
            if solve == 'strict-relaxation': solved = strict_relaxation(case)
            elif solve in ('da', 'full'): solved = solve_dispatch(case, target, full=solve == 'full')
        except Exception as exc:
            solved = dict(failed=True, error_type=type(exc).__name__, error=str(exc),
                          scope='validation_failed_or_incomplete;not_accepted')
        (target/'solve.json').write_text(json.dumps(solved, ensure_ascii=False, indent=2, default=float), encoding='utf-8')
        for stage in preview['assessment']['stages']:
            rows.append({**cell, 'tier':preview['assessment']['tier'], **stage,
                         'solve_termination': solved.get('termination'), 'solve_failed': solved.get('failed', False),
                         'full_day_rt_checked': solved.get('full_day_rt_checked', False)})
        manifest['completed_count'] += 1
        manifest['source_unchanged'] = fingerprint(root) == before
        pd.DataFrame(rows).to_csv(output/'coverage.csv', index=False)
        (output/'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
        print(cell['id'], preview['assessment']['tier'], solved.get('termination', solved.get('error', 'done')), round(time.perf_counter()-start, 2), flush=True)
        if not manifest['source_unchanged']: raise RuntimeError('Source changed during audit; results do not share one frozen source')
    return output


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--solve', choices=['none', 'strict-relaxation', 'da', 'full'], default='none')
    parser.add_argument('--select', nargs='+', help='Combination IDs from coverage, e.g. dry_re0.8_load1.1')
    parser.add_argument('--split-renewables', action='store_true', help='54-cell wind/PV-independent matrix instead of the 18 joint-level cells')
    parser.add_argument('--wind-event', choices=['none', 'evening_lull'], default='none')
    parser.add_argument('--time-limit', type=float, help='Explicit DA diagnostic limit; recorded, not saved to source')
    args = parser.parse_args()
    audit(args.root, args.output, args.solve, args.select, args.time_limit, args.split_renewables, args.wind_event)
