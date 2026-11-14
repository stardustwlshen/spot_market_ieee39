"""Explicit DA-only full-resource pricing research adapter; not official pricing.

No source model mutation, automatic eligibility, RT inventory interpretation,
price clipping, or settlement. See docs31 in the enclosing rebuild project.
"""
from dataclasses import dataclass
import math
from numbers import Real

import pyomo.environ as pyo
from pyomo.core.base.componentuid import ComponentUID

from .gd_lmp import (PricingInputError, UnsupportedPricingCase, PricingSpec,
    Period, PowerPoint, BidTerm, PenaltyParameter, PenaltyTerm, PriceRow,
    ConstraintReview, FixedVariableSource, OmittedDispatchRow)


@dataclass(frozen=True)
class DAReferencePricePolicy:
    delta: float
    network_penalty_yuan_per_mwh: float
    curtailment_penalty_yuan_per_mwh: float
    eligibility: dict
    zero_output_policy: str
    negative_output_policy: str
    parameter_source: str
    eligibility_source: str
    boundary_review_source: str
    constraint_review_source: str


def _number(value, name):
    if isinstance(value, bool) or not isinstance(value, Real) or not math.isfinite(float(value)) or value < 0:
        raise PricingInputError(name + ': finite nonnegative numeric input required')
    return float(value)


def _text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise PricingInputError(name + ': explicit source required')


def _uid(value):
    return str(ComponentUID(value))


def da_resource_inventory(model):
    """Return exact declared physical resource IDs, without inventing eligibility."""
    if not all(hasattr(model, name) for name in ('gd_storage','gd_piecewise_bids','gd_reserve','_energy_inputs')):
        raise UnsupportedPricingCase('full DA adapter requires storage, complete piecewise bids and reserve components')
    bid = model.gd_piecewise_bids
    required = {'renewable','battery','pumped'}
    if set(bid.FAMILY) != required:
        raise UnsupportedPricingCase('complete renewable/battery/pumped piecewise families required')
    renewable = [r.resource_id for r in bid._piecewise_raw_bids if r.kind == 'renewable']
    ids = (*tuple(model.G), *renewable, *tuple(model.gd_storage.BAT), *tuple(model.gd_storage.PLANT))
    if len(set(ids)) != len(ids):
        raise PricingInputError('resource identity collision across physical families')
    return ids


def build_da_reference_pricing_spec(model, context, *, policy):
    if not isinstance(policy, DAReferencePricePolicy):
        raise PricingInputError('explicit DAReferencePricePolicy required')
    if not isinstance(context, dict) or context.get('stage') != 'DA':
        raise UnsupportedPricingCase('DA-only adapter; RT inventory pricing interpretation is unresolved')
    ids = da_resource_inventory(model)
    if model.gd_storage.audit['stage'] != 'DA_SCED' or model.gd_reserve._policy_scope['stage'] != 'DA_SCED':
        raise UnsupportedPricingCase('requires the selected DA_SCED state, not SCUC or RT')
    for key in ('parameter_source','eligibility_source','boundary_review_source','constraint_review_source'):
        _text(getattr(policy,key),key)
    delta = _number(policy.delta,'delta')
    network = _number(policy.network_penalty_yuan_per_mwh,'network penalty')
    curtail = _number(policy.curtailment_penalty_yuan_per_mwh,'curtailment penalty')
    if policy.zero_output_policy != 'literal_relative_zero' or policy.negative_output_policy != 'signed_magnitude_relative':
        raise UnsupportedPricingCase('explicit zero/negative research interpretation required')
    expected = {(u,t) for u in ids for t in model.T}
    if not isinstance(policy.eligibility,dict) or set(policy.eligibility) != expected or any(type(v) is not bool for v in policy.eligibility.values()):
        raise PricingInputError('eligibility must exactly cover all resources/periods with Boolean values')
    if any(type(k[1]) is not int for k in policy.eligibility):
        raise PricingInputError('eligibility periods must be integer IDs')
    if any(not v.fixed for v in model.component_data_objects(pyo.Var) if v.is_integer()):
        raise PricingInputError('source integers must be fixed by selected-state continuous polish')
    s, b = model.gd_storage, model.gd_piecewise_bids
    for component in (model.balance_shortfall, model.balance_surplus):
        if any(not v.fixed or float(pyo.value(v)) != 0. for v in component.values()):
            raise UnsupportedPricingCase('DA adapter requires hard fixed-zero balance slacks; no free unpriced load slack')
    if any(abs(float(pyo.value(v))) > 1e-9 for v in s.band_deviation.values()):
        raise PricingInputError('DA must not contain RT reservoir-band deviations')
    if any(r['anchor_policy']['mode'] != 'hard' for r in s._storage_inputs['batteries'].values()):
        raise UnsupportedPricingCase('emergency storage-anchor pricing policy is not supported')
    powers, bids, penalties, rows = [], [], [], []
    def point(u,t,kind,power,online,lo,hi):
        value = float(pyo.value(power)); eligible = policy.eligibility[u,t]
        options = {}
        if online and eligible and value == 0:
            options.update(zero_output_policy=policy.zero_output_policy,zero_output_source=policy.parameter_source)
        if online and eligible and value < 0 and kind in ('storage','pumped'):
            options.update(negative_output_policy=policy.negative_output_policy,negative_output_source=policy.parameter_source)
        powers.append(PowerPoint(u,str(t),kind,_uid(power),eligible,online,float(lo),float(hi),
            policy.boundary_review_source,policy.eligibility_source,'sced_effective_after_services',**options))
    for t in model.T:
        for u,r in model._energy_inputs['thermal'].items():
            on = bool(round(float(pyo.value(model.online[u,t]))))
            point(u,t,'thermal',model.power[u,t],on,
                pyo.value(model.online_min[u,t])*int(on),pyo.value(model.online_max[u,t])*int(on))
            for k,segment in r['segments'].items():
                bids.append(BidTerm(_uid(model.segment_power[u,k,t]),str(t),segment['price_yuan_per_mwh'][t],r['source']))
        for record in b._piecewise_raw_bids:
            normalized = b.audit['normalized_curves'][record.bid_id]
            for k,price in enumerate(normalized['prices_yuan_per_mwh'][t]):
                bids.append(BidTerm(_uid(b.segment_power[record.bid_id,k,t]),str(t),
                    normalized['objective_sign']*price,record.curve.price_source))
            if record.kind == 'renewable':
                point(record.resource_id,t,'renewable',b.resource_power[record.bid_id,t],True,
                    0.,record.availability_mw[t])
        for u,r in s._storage_inputs['batteries'].items():
            charge = round(float(pyo.value(s.charge_mode[u,t])))
            discharge = round(float(pyo.value(s.discharge_mode[u,t])))
            point(u,t,'storage',s.battery_net_power[u,t],bool(charge or discharge),
                -r['charge_max_mw'][t]*charge,r['discharge_max_mw'][t]*discharge)
        for plant,r in s._storage_inputs['plants'].items():
            lo = -sum(s._storage_inputs['units'][u]['pump_power_mw']*round(float(pyo.value(s.mode[u,t,'pump']))) for u in r['members'])
            hi = sum(s._storage_inputs['units'][u]['generation_max_mw'][t]*round(float(pyo.value(s.mode[u,t,'generate']))) for u in r['members'])
            point(plant,t,'pumped',s.plant_net_power[plant,t],bool(lo or hi),lo,hi)
        for bus in model.B:
            rows.append(PriceRow(bus,str(t),_uid(model.c_balance[bus,t]),_uid(model.demand_mw[bus,t])))
            penalties.append(PenaltyTerm(_uid(model.renewable_curtailment[bus,t]),str(t),'curtailment',policy.parameter_source))
        for family, names, plus, minus in (('line',model.L,model.line_over,model.line_under),
                                          ('section',model.S,model.section_over,model.section_under)):
            for key in names:
                penalties.extend((PenaltyTerm(_uid(plus[key,t]),str(t),'network',policy.parameter_source,family,key,'+'),
                                  PenaltyTerm(_uid(minus[key,t]),str(t),'network',policy.parameter_source,family,key,'-')))
    source = 'GDE2026 5.8.2.3 printed72-78: independent DA pricing omits operating up/down reserve; https://nxepta.com/attached/file/20260205/20260205011253766.pdf'
    return PricingSpec(stage='DA',delta=delta,delta_source=policy.parameter_source,
        periods=tuple(Period(str(t),model._energy_horizon.duration_h[t],policy.parameter_source) for t in model.T),
        powers=tuple(powers),dispatch_inventory=tuple(p.power_ref for p in powers),bid_terms=tuple(bids),
        penalty_parameters=(PenaltyParameter('network',network,policy.parameter_source),PenaltyParameter('curtailment',curtail,policy.parameter_source)),
        penalty_terms=tuple(penalties),price_rows=tuple(rows),
        constraint_reviews=tuple(ConstraintReview(_uid(c),'nodal_balance' if c.parent_component() is model.c_balance else 'physical',policy.constraint_review_source)
            for c in model.component_data_objects(pyo.Constraint,active=True)),
        fixed_variable_sources=tuple(FixedVariableSource(_uid(v),'Selected DA_SCED continuous polish; '+policy.boundary_review_source)
            for v in model.component_data_objects(pyo.Var) if v.fixed),
        source_scope='Full-resource DA96 conditional pricing research; not official integer algorithm or complete98point market',
        coverage_limitations=('Eligibility/delta/penalties are externally declared research inputs, not official parameter certification.',
            'Fixed-state thermal base/start/stop costs are omitted as constants, not charged twice.',
            'RT inventory pricing, final publication, scarcity and settlement are not implemented.'),
        omitted_dispatch_rows=tuple(OmittedDispatchRow(_uid(c),kind,source)
            for component,kind in ((model.gd_reserve.c_up,'DA_operating_up'),(model.gd_reserve.c_down,'DA_operating_down'))
            for c in component.values()))
