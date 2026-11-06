"""Public API example: create once per series, reload without redrawing.

Run from repository root with PYTHONPATH=src. This is process generation only,
not a clearing/state-continuity or forecast-calibration acceptance claim.
"""
from smm.causal_types import ResourceSpec
from smm.causal_processes import ProcessPlan
from smm.causal_provider import compile_provider, save_bundle, load_bundle

plan = ProcessPlan(
    start_utc='2026-07-15T00:00:00Z', day_count=2, timezone='UTC', root_seed='42',
    realization_id='study_1',
    resources=(ResourceSpec('load','load',120.), ResourceSpec('W1','wind',30.,'wind_A'),
               ResourceSpec('PV1','pv',20.,'pv_A'), ResourceSpec('H1','natural_inflow',50.,'catchment_A')),
    day_specs={'2026-07-15':{'wind':{'preset':'wind_low'},'pv':{'preset':'pv_broken_cloud'},
                           'load':{'preset':'load_normal'},'natural_inflow':{'preset':'inflow_rising'}},
               '2026-07-16':{'wind':{'preset':'wind_ramp','parameters':{'target_end':.7}},
                           'pv':{'preset':'pv_clear'},'load':{'preset':'load_high'},
                           'natural_inflow':{'preset':'inflow_recession'}}})
policies={'2026-07-15':dict(wind='baseline',pv='delayed',load='responsive',natural_inflow='baseline'),
          '2026-07-16':dict(wind='responsive',pv='baseline',load='baseline',natural_inflow='delayed')}

if __name__=='__main__':
    actual,provider=compile_provider(plan,forecast_policy_by_date=policies)
    print(provider.source_manifest())
    print(provider.day_summary(plan.start_utc))
    selected=provider.select_day(plan.start_utc,0,0,stage='DA',executed_through_period=0)
    print(selected.forecast_id, selected.values('H1')[:4])
    # Optional explicit new-directory freeze, then fresh runtime provider:
    # save_bundle('new_source_bundle',actual,forecast_policy_by_date=policies)
    # actual,provider=load_bundle('new_source_bundle')
    # build_da_case(cfg,net,public_schedule_profiles,daily_bids,
    #     input_provider=provider,day_start_utc=plan.start_utc, init=actual_day_state,
    #     require_complete_bids=True)  # last two kwargs supplied by series-engine patch
