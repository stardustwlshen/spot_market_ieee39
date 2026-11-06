"""Machine-readable, fail-closed contract for the completed v3 consumer layer.

This flag describes installed input-consumption support, NOT scenario feasibility,
series-state engine readiness, geographic calibration or a statistical guarantee.
"""
from __future__ import annotations
import inspect

CONTRACT_VERSION='causal_inputs_qin_v3_1'


def consumer_capabilities():
    from .case import DACase,build_da_case
    from .rolling import make_window_case,value_method_terms,hydro_terminal_lookahead_terms
    from .hydro_daily_energy import audit
    from .fixed_load import nodal_demand
    missing=[]
    for field in ('input_provider','selected_inputs','day_start_utc'):
        if field not in DACase.__dataclass_fields__: missing.append('DACase.'+field)
    for fn,required in ((build_da_case,('input_provider','selected_inputs','day_start_utc')),
                        (make_window_case,('selected_inputs',)),
                        (value_method_terms,('selected_inputs','day_start_utc')),
                        (hydro_terminal_lookahead_terms,('selected_inputs','day_start_utc')),
                        (audit,('input_registry',)),(nodal_demand,('selected_inputs',))):
        params=inspect.signature(fn).parameters
        missing.extend(fn.__name__+'.'+p for p in required if p not in params)
    return dict(contract_version=CONTRACT_VERSION,ready=not missing,missing=missing,
                grid_minutes=15,natural_coverage='remaining_day_same_issue',
                physical_inputs=('wind','pv','load','local_natural_inflow'),
                current_interval_policy='explicit_perfect_mean_research_observation',
                legacy_manual_inputs='unchanged_legacy_entrypoints',
                v3_source_modes=('frozen_generated_bundle',),
                requires_separate_series_engine_readiness=True)
