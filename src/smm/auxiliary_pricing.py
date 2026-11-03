"""Opt-in standalone bounded auxiliary pricing; never an execution dispatch.

When enabled, DA/RT entry points call this module after formal SCED. Prices are duals under
modified soft-constraint penalties and full-horizon dispatch proximity bands.
They must not replace physical quantities, costs, states, or settlement silently.
An explicitly selected RT remove policy excludes only DA-state tracking from
the clone; actual-state physics and declared DA terminal conditions remain.
"""
from copy import deepcopy

import numpy as np
import pyomo.environ as pyo

from . import scuc



def _integers(m):
    values = {}
    for name in scuc.INT_VARS:
        for index, v in getattr(m, name).items():
            value = float(pyo.value(v))
            if not v.fixed or not np.isfinite(value) or abs(value - round(value)) > 1e-6:
                raise ValueError(f'auxiliary pricing requires fixed integer {v.name}')
            values[(name, index)] = value
    for v in m.component_data_objects(pyo.Var):
        if (v.is_binary() or v.is_integer()) and not v.fixed:
            raise ValueError(f'auxiliary pricing cannot wake unfixed integer {v.name}')
    return values


def _powers(m):
    for kind, name in (('thermal', 'P'), ('renewable', 'Pw'), ('hydro', 'Ph')):
        for (unit, t), value in getattr(m, name).items():
            yield kind, unit, t, value
    for unit in m.S:
        for t in m.T:
            yield 'storage_net', unit, t, m.Pd[unit, t] - m.Pc[unit, t]
    for unit in m.PS:
        for t in m.T:
            yield 'pumped_net', unit, t, m.Pg[unit, t] - m.Pp[unit, t]


def _differences(dispatch, auxiliary, names):
    rows = []
    for name in names:
        for index, v in getattr(dispatch, name).items():
            before = float(pyo.value(v))
            after = float(pyo.value(getattr(auxiliary, name)[index]))
            rows.append(dict(variable=name, index=list(index) if isinstance(index, tuple) else index,
                             dispatch=before, auxiliary=after, difference=after-before))
    return rows


def _prices(m, source):
    raw = deepcopy(scuc.extract_prices(m))
    limits = m._case.cfg.mode['price_limits']
    return dict(source=source, raw=raw,
                lmp_clipped=np.clip(raw['lmp'], limits['clear_min'], limits['clear_max']),
                clipping=dict(min=limits['clear_min'], max=limits['clear_max']),
                settlement_applied=False)


def _override_anchor_costs(m, specification):
    """Clone-only correction on discharge MWh: E*eta_d, Vu*e_gen, V/(.36*water_rate)."""
    if specification.get('anchor_penalty_yuan_per_discharge_mwh') is None:
        return []
    coefficient = specification['anchor_penalty_yuan_per_discharge_mwh']
    if (isinstance(coefficient, bool) or not isinstance(coefficient, (int, float))
            or not np.isfinite(coefficient) or coefficient < 0):
        raise ValueError('anchor_penalty_yuan_per_discharge_mwh must be finite nonnegative')
    correction, rows = 0, []
    for i, (unit, t, lower, upper, kind) in enumerate(m._anchors):
        if kind not in ('E', 'Vu', 'V'):
            raise ValueError(f'unsupported anchor kind {kind}')
        conversion = (float(m._md.storage[unit]['eta_d']) if kind == 'E' else
                      float(m._md.pumped[unit]['e_gen']) if kind == 'Vu' else
                      1. / (.36 * float(m._md.hydro[unit]['water_rate'])))
        if not np.isfinite(conversion) or conversion <= 0:
            raise ValueError('anchor discharge conversion eta_d/e_gen must be finite positive')
        old = float(m._anchor_cost_coefficients[i])
        new = coefficient * conversion
        correction += (new-old) * (m.sA_up[i] + m.sA_dn[i])
        m._anchor_cost_coefficients[i] = new
        rows.append(dict(anchor_index=i, unit=unit, kind=kind, relative_period=int(t),
                         native_unit='MWh' if kind == 'E' else '1e4m3',
                         discharge_mwh_per_native_unit=conversion, old_yuan_per_native_unit=old,
                         new_yuan_per_native_unit=new, coefficient_yuan_per_discharge_mwh=coefficient,
                         lower=lower, upper=upper))
    m.obj.set_value(m.obj.expr + correction)
    return rows


def _remove_rt_tracking(m):
    """Remove only DA-state tracking from this RT pricing clone, never physical bounds."""
    rows, correction = [], 0
    for i, (unit, t, lower, upper, kind) in enumerate(m._anchors):
        conversion = (float(m._md.storage[unit]['eta_d']) if kind == 'E' else
                      float(m._md.pumped[unit]['e_gen']) if kind == 'Vu' else
                      1. / (.36 * float(m._md.hydro[unit]['water_rate'])))
        coefficient = m._anchor_cost_coefficients[i]
        correction += coefficient * (m.sA_up[i] + m.sA_dn[i])
        m._anchor_cost_coefficients[i] = 0.
        m.c_anc_hi[i].deactivate()
        m.c_anc_lo[i].deactivate()
        m.sA_up[i].fix(0.)
        m.sA_dn[i].fix(0.)
        rows.append(dict(unit=unit, relative_period=int(t), kind=kind, lower=lower, upper=upper,
                         discharge_mwh_per_native_unit=conversion, removed_cost_coefficient=coefficient,
                         removed_constraint='soft_tracking', slack_zero_means_target_met=False))
    if hasattr(m, 'c_hydro_window_end'):
        for h, con in m.c_hydro_window_end.items():
            target = float(pyo.value(con.lower))
            con.deactivate()
            rows.append(dict(unit=h, relative_period=m._md.T-1, kind='V', lower=target, upper=target,
                             discharge_mwh_per_native_unit=1./(.36*float(m._md.hydro[h]['water_rate'])),
                             removed_cost_coefficient=0., removed_constraint='hard_rt_da_tracking',
                             slack_zero_means_target_met=False))
    m.obj.set_value(m.obj.expr - correction)
    return rows


def _freeze_hydro_spill(dispatch, auxiliary):
    """Preserve formal dispatch/planned water disposal decisions at every pricing-horizon point."""
    expected = {(h, t) for h in dispatch.HS for t in dispatch.T}
    if (set(dispatch.Qspill) != expected or set(auxiliary.Qspill) != expected
            or set(auxiliary.HS) != set(dispatch.HS) or set(auxiliary.T) != set(dispatch.T)):
        raise ValueError('hydro spill fixation requires complete matching station/time coverage')
    rows = []
    for h in dispatch.HS:
        for t in dispatch.T:
            value = float(pyo.value(dispatch.Qspill[h, t]))
            if not np.isfinite(value):
                raise ValueError('formal hydro spill must be finite before auxiliary fixation')
            auxiliary.Qspill[h, t].fix(value)
            rows.append(dict(station=h, relative_period=int(t), period=dispatch._case.t0+int(t),
                             dispatch_spill_m3s=value))
    return rows


def run_auxiliary_pricing(dispatch_model):
    """Clone completed fixed-integer SCED, solve bounded pricing LP or raise.

    Every modeled resource and every relative time has the symmetric signed
    band max(3% * abs(dispatch MW), 1 MW). Storage/pumped bands constrain net
    output, not separately enlarged charge/discharge ranges. Existing physical
    modes, physical intertemporal constraints and existing slack budgets survive.
    Hydro Qspill is fixed to formal dispatch at every horizon point; V/Ph remain
    governed by unchanged water dynamics and power bands, not state fixation.
    Explicit RT tracking removal deactivates DA-state targets only; the legacy
    preserve policy instead retains targets and optionally overrides their cost.
    This standalone research interface supports pure energy/reserve only, not AGC.
    Exceptions propagate without substituting physical prices or fake results.
    """
    from .config import validate_auxiliary_pricing
    mode = dispatch_model._case.cfg.mode
    validate_auxiliary_pricing(mode)
    if "auxiliary_pricing" not in mode:
        raise ValueError("explicit auxiliary_pricing configuration is required")
    specification = deepcopy(mode["auxiliary_pricing"])
    band = specification["dispatch_band"]
    if getattr(dispatch_model, "_agc", {}).get("enabled", False):
        raise ValueError("auxiliary pricing currently supports AGC-disabled models only")
    original_integers = _integers(dispatch_model)
    if not hasattr(dispatch_model, 'dual'):
        raise ValueError('auxiliary pricing requires a completed SCED with duals')
    dispatch_prices = _prices(dispatch_model, 'physical_fixed_integer_sced')
    before = {v.name: (float(pyo.value(v)), v.fixed) for v in dispatch_model.component_data_objects(pyo.Var)}
    if any(not np.isfinite(value) for value, _ in before.values()):
        raise ValueError('dispatch must contain finite completed SCED values')
    auxiliary = dispatch_model.clone()
    if hasattr(dispatch_model, "_solver_budget"):
        auxiliary._solver_budget = dispatch_model._solver_budget  # same shared ledger, never a cloned allowance
        auxiliary._solver_budget_phase = "auxiliary_pricing"
    spill_fixation = _freeze_hydro_spill(dispatch_model, auxiliary)
    if hasattr(auxiliary, 'auxiliary_dispatch_bands'):
        raise ValueError('cannot price an auxiliary pricing model recursively')
    auxiliary.auxiliary_dispatch_bands = pyo.ConstraintList()
    bands = []
    for kind, unit, t, expression in _powers(auxiliary):
        dispatch = float(pyo.value(expression))
        epsilon = max(band["relative_to_dispatch"] * abs(dispatch), band["minimum_mw"])
        lower, upper = dispatch-epsilon, dispatch+epsilon
        auxiliary.auxiliary_dispatch_bands.add(pyo.inequality(lower, expression, upper))
        bands.append(dict(resource_type=kind, unit=unit, relative_period=int(t),
                          period=dispatch_model._case.t0+int(t), dispatch_mw=dispatch,
                          epsilon_mw=epsilon, lower_mw=lower, upper_mw=upper))
    penalties = dict(auxiliary._pen)
    penalties.update(specification["penalties_yuan_per_mwh"])
    scuc.set_penalties(auxiliary, penalties)  # clone only
    remove_tracking = specification.get('rt_tracking_policy', 'preserve') == 'remove' and dispatch_model._case.stage == 'RT'
    tracking = _remove_rt_tracking(auxiliary) if remove_tracking else []
    anchors = [] if remove_tracking else _override_anchor_costs(auxiliary, specification)
    info = scuc.solve_sced(auxiliary)
    if _integers(auxiliary) != original_integers:
        raise RuntimeError('auxiliary pricing changed fixed integer decisions')
    for row, (_, _, _, expression) in zip(bands, _powers(auxiliary)):
        value = float(pyo.value(expression))
        row.update(auxiliary_mw=value, deviation_mw=value-row['dispatch_mw'],
                   lower_binding=abs(value-row['lower_mw']) <= 1e-6,
                   upper_binding=abs(value-row['upper_mw']) <= 1e-6)
        if not row['lower_mw']-1e-6 <= value <= row['upper_mw']+1e-6:
            raise RuntimeError('auxiliary pricing violates dispatch proximity band')
    after = {v.name: (float(pyo.value(v)), v.fixed) for v in dispatch_model.component_data_objects(pyo.Var)}
    if after != before:
        raise RuntimeError('physical dispatch was mutated')
    for row in spill_fixation:
        variable = auxiliary.Qspill[row['station'], row['relative_period']]
        value = float(pyo.value(variable))
        row.update(auxiliary_spill_m3s=value, difference_m3s=value-row['dispatch_spill_m3s'], fixed=bool(variable.fixed))
        if not variable.fixed or not np.isfinite(value) or abs(row['difference_m3s']) > 1e-9:
            raise RuntimeError('auxiliary hydro spill changed despite formal-dispatch fixation')
    for row in tracking:
        for label, model in (('dispatch', dispatch_model), ('auxiliary', auxiliary)):
            value = float(pyo.value(getattr(model, row['kind'])[row['unit'], row['relative_period']]))
            row[label+'_state_native'] = value
            row[label+'_target_deviation_native'] = value - min(max(value, row['lower']), row['upper'])
            row[label+'_target_deviation_discharge_mwh'] = row[label+'_target_deviation_native'] * row['discharge_mwh_per_native_unit']
    for row in anchors:
        i = row['anchor_index']
        for label, model in (('dispatch', dispatch_model), ('auxiliary', auxiliary)):
            native_slack = float(pyo.value(model.sA_up[i]) + pyo.value(model.sA_dn[i]))
            state = float(pyo.value(getattr(model, row['kind'])[row['unit'], row['relative_period']]))
            residual = max(row['lower']-state, state-row['upper'], 0.)
            row[label+'_slack_native'] = native_slack
            row[label+'_equivalent_discharge_slack_mwh'] = native_slack * row['discharge_mwh_per_native_unit']
            row[label+'_equivalent_discharge_residual_mwh'] = residual * row['discharge_mwh_per_native_unit']
            row[label+'_anchor_cost_yuan'] = native_slack * row['old_yuan_per_native_unit' if label == 'dispatch' else 'new_yuan_per_native_unit']
    return dict(model=auxiliary, dispatch_prices=dispatch_prices, anchor_costs=anchors, removed_rt_tracking=tracking,
                hydro_spill_fixation=spill_fixation, hydro_spill_policy="fixed_to_formal_dispatch_all_horizon",
                auxiliary_prices=_prices(auxiliary, 'bounded_auxiliary_pricing'),
                solve_info=info, bands=bands, penalties_yuan_per_mwh=specification["penalties_yuan_per_mwh"],
                configuration=specification, operational_enabled=bool(specification["enabled"]), invocation="pipeline" if specification["enabled"] else "manual_standalone_diagnostic",
                integer_invariance=True, original_dispatch_unchanged=True,
                dispatch_slack=scuc.slack_summary(dispatch_model), auxiliary_slack=scuc.slack_summary(auxiliary),
                slack_differences=_differences(dispatch_model, auxiliary, ('shed','surplus','sl','sR_up','sR_dn','sA_up','sA_dn')),
                max_absolute_power_deviation_mw=max((abs(r['deviation_mw']) for r in bands), default=0.0),
                reserve_differences=_differences(dispatch_model, auxiliary, ('r_up','r_dn','rh_up','rh_dn','rs_up','rs_dn','rp_up','rp_dn')),
                state_differences=_differences(dispatch_model, auxiliary, ('E','V','Vu')),
                physical_dispatch_source='input_sced', auxiliary_quantities_executable=False)


def numeric_diagnostics(result):
    """Detached JSON-friendly diagnostics; never serialize a solver model."""
    def convert(value):
        if isinstance(value, np.ndarray):
            return convert(value.tolist())
        if isinstance(value, np.generic):
            return convert(value.item())
        if isinstance(value, dict):
            return {str(k): convert(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [convert(v) for v in value]
        if isinstance(value, float) and not np.isfinite(value):
            return None
        return value
    return convert({key: value for key, value in result.items() if key != 'model'})
