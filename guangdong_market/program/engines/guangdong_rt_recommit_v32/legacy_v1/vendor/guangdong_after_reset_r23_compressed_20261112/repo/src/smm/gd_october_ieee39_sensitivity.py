"""Qualification-only necessary-condition sensitivity, never a new dispatch.

The baseline model remains untouched. We add the maximum explicitly hypothetical
capacity credits to its fixed positive-reserve inequality. A negative bound
proves that these credits alone cannot repair that period; passing does not
establish feasible dispatch, standby readiness, or actual qualification.
"""
import pyomo.environ as pyo
from .gd_october_ieee39_scenario import constant_positive_reserve_screen


def qualification_sensitivity(bundle, model):
    baseline=constant_positive_reserve_screen(model)
    if baseline['status']=='not_constant':raise ValueError('constant baseline capacity required')
    pumped=sum(float(p['unit_gen_max'])*p['n_units'] for p in bundle['static']['units']['pumped'])
    rows=[]
    for t in model.T:
        r=model.c_positive_reserve[t]
        margin=float(pyo.value(r.body)-pyo.value(r.lower)) if r.lower is not None else float(pyo.value(r.upper)-pyo.value(r.body))
        renewable=sum(float(pyo.value(model.renewable_available[b,t])) for b in model.B)
        rows.append(dict(period=int(t),baseline_margin_mw=margin,hypothetical_forecast_credit_mw=renewable,
            hypothetical_pumped_standby_credit_mw=pumped,sensitivity_margin_mw=margin+renewable+pumped))
    violations=[r for r in rows if r['sensitivity_margin_mw'] < -1e-7]
    return dict(schema_version='gd.october.ieee39_qualification_sensitivity.v1',
        name='Same forecasts and initials, full renewable forecast plus original pumped standby capacity',
        source_kind='research_assumption',assumption_sha256=bundle['assumption_sha256'],baseline_model_sha256=model._gd_october_signature,
        hypotheses=['Credit every wind/PV forecast MW toward positive reserve capacity, not a verification of qualification.',
            'Credit original PS1 two units at 300 MW each as off-state standby; readiness, preparation, ramp and water delivery are not certified.',
            'Keep all forecasts, load, 400 MW requirement, initial states, gas events and physical inventory unchanged.'],
        rows=rows,first_period_margin_mw=rows[0]['sensitivity_margin_mw'],minimum_margin_mw=min(r['sensitivity_margin_mw'] for r in rows),
        status='still_structurally_infeasible' if violations else 'necessary_capacity_condition_only_passes',violated_period_count=len(violations),
        alternate_model_built=False,baseline_model_changed=False,solver_called=False,numerical_feasibility_verified=False,
        operator_confirmation=None,official_market_accepted=False,
        explanation='This is a capacity upper-bound sensitivity, not a feasible alternative operating plan. Boundary-import or pre-day commitment changes require a separately declared future counterfactual.')
