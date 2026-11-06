"""Explicit synthetic assumptions, never empirical calibration or probabilities."""
from copy import deepcopy
import math
from .causal_types import freeze

WIND = {
 'wind_low': dict(target=.25, slow_h=8., fast_h=.5, slow_sigma=.30, fast_sigma=.10, rho_slow=.6, rho_fast=.2),
 'wind_normal': dict(target=.50, slow_h=6., fast_h=.5, slow_sigma=.40, fast_sigma=.12, rho_slow=.6, rho_fast=.2),
 'wind_high': dict(target=.75, slow_h=8., fast_h=.5, slow_sigma=.30, fast_sigma=.10, rho_slow=.6, rho_fast=.2),
 'wind_ramp': dict(target=.25, target_end=.75, event_hour=12., duration_h=4., slow_h=2., fast_h=.5, slow_sigma=.25, fast_sigma=.10, rho_slow=.7, rho_fast=.2),
 'wind_volatile': dict(target=.50, slow_h=3., fast_h=.25, slow_sigma=.50, fast_sigma=.20, rho_slow=.5, rho_fast=.3)}
PV = {
 'pv_clear': dict(cloud_mode='clear', clear_k=.94, shaded_k=.28, clear_h=1.5, shaded_h=.75, response_h=.5, sigma=.08, rho=.6),
 'pv_overcast': dict(cloud_mode='shaded', clear_k=.94, shaded_k=.28, clear_h=1.5, shaded_h=.75, response_h=.75, sigma=.18, rho=.7),
 'pv_broken_cloud': dict(cloud_mode='switching', clear_k=.88, shaded_k=.35, clear_h=1.5, shaded_h=.75, response_h=.25, sigma=.20, rho=.6),
 'pv_fast_cloud': dict(cloud_mode='switching', clear_k=.90, shaded_k=.25, clear_h=.4, shaded_h=.3, response_h=.10, sigma=.25, rho=.5)}
LOAD = {
 'load_normal': dict(target=1., slow_h=12., fast_h=1., slow_sigma=.035, fast_sigma=.010, peak_shift_h=0.),
 'load_low': dict(target=.9, slow_h=12., fast_h=1., slow_sigma=.030, fast_sigma=.010, peak_shift_h=0.),
 'load_high': dict(target=1.12, slow_h=12., fast_h=1., slow_sigma=.040, fast_sigma=.012, peak_shift_h=0.),
 'load_peak_shift': dict(target=1., slow_h=8., fast_h=1., slow_sigma=.035, fast_sigma=.010, peak_shift_h=1.),
 'load_volatile': dict(target=1., slow_h=6., fast_h=.5, slow_sigma=.060, fast_sigma=.020, peak_shift_h=0.)}
WATER = {
 'inflow_steady': dict(target=1., amplitude=0., duration_h=12., event_hour=0., base_h=48., rise_h=4., fall_h=18., route_h=3.),
 'inflow_dry': dict(target=.5, amplitude=0., duration_h=12., event_hour=0., base_h=48., rise_h=6., fall_h=24., route_h=4.),
 'inflow_rising': dict(target=1., amplitude=1., duration_h=12., event_hour=6., base_h=24., rise_h=3., fall_h=18., route_h=3.),
 'inflow_pulse': dict(target=1., amplitude=2., duration_h=18., event_hour=6., base_h=24., rise_h=2., fall_h=24., route_h=4.),
 'inflow_recession': dict(target=.5, amplitude=0., duration_h=12., event_hour=0., base_h=24., rise_h=4., fall_h=24., route_h=4.)}
for _p in WATER.values():
    _p.update(noise_h=12., noise_amplitude=.08, rho=.5, lag_h=0.)
PRESETS = freeze(dict(wind=WIND, pv=PV, load=LOAD, natural_inflow=WATER))
DEFAULTS = dict(wind='wind_normal', pv='pv_broken_cloud', load='load_normal', natural_inflow='inflow_steady')
BOUNDS = {
 'wind': dict(target=(.05,.95), target_end=(.05,.95), event_hour=(0.,24.), duration_h=(1.,12.), slow_h=(2.,24.), fast_h=(1/6,2.), slow_sigma=(0.,.8), fast_sigma=(0.,.35), rho_slow=(0.,.95),rho_fast=(0.,.95)),
 'pv': dict(clear_k=(.05,.99),shaded_k=(.05,.99),clear_h=(1/6,6.),shaded_h=(1/6,6.),response_h=(1/12,2.),sigma=(0.,.5),rho=(0.,.95)),
 'load': dict(target=(.8,1.25),slow_h=(4.,48.),fast_h=(.25,4.),slow_sigma=(0.,.10),fast_sigma=(0.,.04),peak_shift_h=(-2.,2.)),
 'natural_inflow': dict(target=(0.,4.),amplitude=(0.,4.),duration_h=(2.,72.),event_hour=(0.,24.),base_h=(12.,168.),rise_h=(.5,24.),fall_h=(2.,168.),route_h=(.5,48.),noise_h=(2.,72.),noise_amplitude=(0.,.3),rho=(0.,.95),lag_h=(0.,24.))}
POLICIES = freeze({
 'responsive':dict(level=.80,trend=.05,sigma_multiplier=.5,beta_multiplier=0.,delay_minutes=(0,0,0,0)),
 'baseline':dict(level=.55,trend=.03,sigma_multiplier=1.,beta_multiplier=0.,delay_minutes=(15,15,15,60)),
 'delayed':dict(level=.20,trend=.01,sigma_multiplier=2.,beta_multiplier=0.,delay_minutes=(60,30,60,180)),
 'persistent_high':dict(level=.55,trend=.03,sigma_multiplier=1.,beta_multiplier=2.,delay_minutes=(15,15,15,60)),
 'persistent_low':dict(level=.55,trend=.03,sigma_multiplier=1.,beta_multiplier=-2.,delay_minutes=(15,15,15,60)),
 'turning_point_lag':dict(level=.25,trend=0.,sigma_multiplier=1.5,beta_multiplier=0.,delay_minutes=(15,15,15,60))})
FORECAST = freeze(dict(
 wind=dict(mean_h=12.,trend_h=1.,max_trend=.4,bias_h=6.,bias_tau_h=6.,sigma=.12),
 pv=dict(mean_h=2.,trend_h=.25,max_trend=1.,bias_h=1.,bias_tau_h=2.,sigma=.12),
 load=dict(mean_h=24.,trend_h=2.,max_trend=.03,bias_h=12.,bias_tau_h=12.,sigma=.010),
 natural_inflow=dict(mean_h=72.,trend_h=6.,max_trend=.05,bias_h=24.,bias_tau_h=24.,sigma=.04)))


def resolve_actual(kind, spec=None):
    if spec is None:
        spec = DEFAULTS[kind]
    if isinstance(spec, str):
        spec = dict(preset=spec)
    unknown = set(spec)-{'preset','parameters'}
    if unknown:
        raise ValueError(f'unknown actual specification keys: {sorted(unknown)}')
    preset = spec.get('preset',DEFAULTS[kind])
    if preset not in PRESETS[kind]:
        raise ValueError(f'unknown {kind} preset: {preset}')
    result = dict(PRESETS[kind][preset])
    overrides = spec.get('parameters',{})
    if set(overrides)-set(result):
        raise ValueError('unknown or inapplicable process parameter')
    result.update(overrides)
    for key, value in result.items():
        if key == 'cloud_mode':
            if value not in ('clear','shaded','switching'):
                raise ValueError('invalid cloud mode')
            continue
        lo,hi=BOUNDS[kind][key]
        if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or not lo <= value <= hi:
            raise ValueError(f'{kind}.{key} must lie in [{lo}, {hi}]')
    return result


def capabilities():
    try:
        from .causal_integration import consumer_capabilities
        consumer=consumer_capabilities()
    except ImportError:
        consumer=dict(ready=False,contract_version=None,missing=['causal consumer layer not installed'])
    return dict(model='causal_physical_v3', calibrated=False, grid_minutes=15,
                actual_presets={k:list(v) for k,v in PRESETS.items()}, forecast_policies=list(POLICIES),
                parameter_schema={k:dict(bounds=BOUNDS[k],presets={name:dict(p) for name,p in v.items()}) for k,v in PRESETS.items()},
                consumer_integration_ready=consumer['ready'],consumer_contract=consumer,
                current_interval_measurement='explicit_execution_research_policy',
                optimization='one_deterministic_trajectory_per_issue')
