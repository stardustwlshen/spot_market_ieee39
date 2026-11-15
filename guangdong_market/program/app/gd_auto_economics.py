"""Normal receipt path: source-bound executed costs and separate AGC estimate.
No optimizer, no UI interaction, no invented reserve qualification or measured mileage.
"""
from pathlib import Path
from copy import deepcopy
import hashlib,json
from decimal import Decimal
from gd_default_costs import defaults_for
from gd_unit_economics import executed_events
from gd_service_estimates import collect,estimate

def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def prepare(hour, mileage_per_capacity_hour=10, reserve_parameters=None):
    periods={(r['start'],r['end'])for r in hour['rows']}
    if len(periods)!=1:raise ValueError('one hour scope required')
    pins=dict(hour.get('source_pins',{}));view=dict(run_id=hour['run_id'],source_manifest=pins,execution_event_summaries=[])
    errors=[];execution=None;case=None
    candidates=[p for p in pins if Path(p).name.startswith('EXECUTION_H') and Path(p).suffix=='.json']
    for path in candidates:
        if sha(path)!=pins[path]:raise ValueError('execution source changed')
        e=json.loads(Path(path).read_bytes())
        if e.get('run_id')!=hour['run_id']:raise ValueError('execution source run mismatch')
        if hour.get('input_binding',{}).get('execution_sha256')!=pins[path]:continue
        if execution is not None:raise ValueError('ambiguous executed hour source')
        execution=e;summary=executed_events(hour,e);summary.update(source_path=path,source_sha256=pins[path]);view['execution_event_summaries'].append(summary)
    if execution is None:errors.append('no hash-bound executed hour; startup costs cannot be inferred')
    rows=deepcopy(hour['rows'])
    if execution:
        casepath=str((Path(execution['base_run'])/'seed/case.json').resolve())
        expected=execution.get('source_pins',{}).get(casepath)
        if expected:
            if sha(casepath)!=expected:raise ValueError('capacity source changed')
            from gd_direct_sced_prices import _unpack
            case=_unpack(json.loads(Path(casepath).read_bytes())['value']);pins[casepath]=expected
            for row in rows:
                if row['resource']in case['energy_inputs']['thermal']:
                    maximum=case['energy_inputs']['thermal'][row['resource']]['max_mw'];row['capacity_mw']=max(maximum.values())
    selected=dict(rows=rows,periods=periods);costs={r:defaults_for(view,selected,r)for r in {x['resource']for x in rows}}
    estimates={}
    if execution:
        try:
            view['agc_estimate_sources']=collect(hour,execution,pins)
            estimates=estimate(view,periods,costs,mileage_per_capacity_hour)
            if not estimates:errors.append('no complete source-bound AGC hourly award for estimate')
        except (OSError,KeyError,ValueError)as exc:errors.append('AGC estimate unavailable: '+str(exc))
    reserve=None
    if execution is not None and case is not None and reserve_parameters is not False:
        from gd_reserve_estimate import calculate
        reserve=calculate(hour,execution,case,reserve_parameters)
    return dict(reserve_estimate=reserve,costs=costs,agc_estimates=estimates,source_pins=pins,diagnostics=errors,
                scope='research fuel/start cost + separate AGC mileage estimate; no formal reserve or measured AGC invented')

def attach_estimates(economics,prepared):
    for row in economics.get('rows',[]):
        value=prepared['agc_estimates'].get(row['resource'])
        row['agc_estimate']=value
        row['estimated_energy_plus_agc_margin_CNY']=None
        # Actual supplied AGC revenue, if any, already belongs to actual service totals.
        # Never add an estimate as a second actual AGC payment.
        if value is not None and row.get('energy_contribution_CNY') is not None:
            row['estimated_energy_plus_agc_margin_CNY']=str(Decimal(str(row['energy_contribution_CNY']))+Decimal(value['amount_CNY']))
    reserve=prepared.get('reserve_estimate')
    by_resource={r['resource']:r for r in (reserve or {}).get('rows',[])}
    for row in economics.get('rows',[]):
        r=by_resource.get(row['resource'])
        row['reserve_estimate']=r
        row['estimated_spot_margin_before_service_allocation_CNY']=None
        reserve_amount=row.get('reserve_revenue_CNY') if row.get('reserve_revenue_CNY') is not None else (r or {}).get('amount_CNY')
        agc_amount=row.get('agc_revenue_CNY') if row.get('agc_revenue_CNY') is not None else (row.get('agc_estimate') or {}).get('amount_CNY')
        row['simulated_reserve_gross_revenue_CNY']=reserve_amount
        if reserve_amount is not None and agc_amount is not None and row.get('energy_contribution_CNY') is not None:
            row['estimated_spot_margin_before_service_allocation_CNY']=str(Decimal(str(row['energy_contribution_CNY']))+Decimal(str(agc_amount))+Decimal(str(reserve_amount)))
        row['service_allocation_included']=False
    economics['reserve_estimate']=reserve
    economics['automatic_input_diagnostics']=prepared['diagnostics'];economics['automatic_input_source_pins']=prepared['source_pins']
    economics['agc_estimate_is_measured']=False
    return economics
