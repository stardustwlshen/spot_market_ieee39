"""Genuine HiGHS call receipt and guarded independent October research prices.

No source SCED status or primal is invented. Only an optimal continuous LP with
complete finite duals and checked KKT conditions can issue a result. Receipts
are process-local traceability, not a security boundary against hostile Python.
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
from math import fsum, isfinite
from weakref import WeakKeyDictionary
import pyomo.environ as pyo

from . import highs_solver
from .gd_october_pricing_inputs import canonical, num, require
from .gd_october_pricing_model import assert_october_pricing_structure
from .gd_october_pricing_rules import NetworkMultiplier, october_lmp_components

_ISSUED=WeakKeyDictionary()
_ORIGINAL_ASSERT=assert_october_pricing_structure
_ORIGINAL_FORMULA=october_lmp_components


@dataclass(frozen=True, eq=False)
class OctoberPricingSolve:
    status: str
    artifact: bytes

    @property
    def report(self):
        return json.loads(self.artifact)


def _snapshot(model):
    values=[(v.name,None if v.value is None else float(v.value)) for v in model.component_data_objects(pyo.Var)]
    dual=[(c.name,None if c not in model.dual else float(model.dual[c])) for c in model.component_data_objects(pyo.Constraint,active=True)]
    return canonical(dict(primal=values,dual=dual))


def _audit_optimum(model, options):
    payload,variables,rows=highs_solver.serialize_model(model,options)
    require(all(v['kind']=='C' for v in payload['variables']),'optimal continuous LP required')
    values=[num(v.value,'complete finite primal') for v in variables]
    duals=[num(model.dual.get(r),'complete finite row dual') for r in rows]
    objective=float(pyo.value(model.objective))
    highs_solver.validate_solution(payload,dict(primal=values,objective=objective,bound=objective))
    reduced=[0.]*len(values)
    for i,v in payload['objective']['terms']:reduced[i]+=v
    dual_objective=payload['objective']['constant']
    max_stationarity=0.; max_complementarity=0.
    for row,y in zip(payload['rows'],duals):
        a=fsum(values[i]*v for i,v in row['terms']); lo,hi=row['lb'],row['ub']
        tolerance=1e-6*max(1,abs(a),abs(lo or 0),abs(hi or 0))
        if lo is not None and hi is not None and lo==hi:
            dual_objective+=y*lo
        elif y>1e-7:
            require(lo is not None,'positive dual on an upper-only constraint')
            require(abs(a-lo)<=tolerance,'positive dual has nonbinding lower row')
            dual_objective+=y*lo; max_complementarity=max(max_complementarity,abs(y*(a-lo)))
        elif y< -1e-7:
            require(hi is not None,'negative dual on a lower-only constraint')
            require(abs(a-hi)<=tolerance,'negative dual has nonbinding upper row')
            dual_objective+=y*hi; max_complementarity=max(max_complementarity,abs(y*(a-hi)))
        for i,v in row['terms']:reduced[i]-=y*v
    for value,r,rc in zip(values,payload['variables'],reduced):
        lo,hi=r['lb'],r['ub']; tol=1e-6*max(1,abs(value),abs(lo or 0),abs(hi or 0))
        if lo is not None and hi is not None and lo==hi:
            dual_objective+=rc*lo
        elif rc>1e-6:
            require(lo is not None and abs(value-lo)<=tol,'positive reduced gradient away from lower bound')
            dual_objective+=rc*lo; max_complementarity=max(max_complementarity,abs(rc*(value-lo)))
        elif rc< -1e-6:
            require(hi is not None and abs(value-hi)<=tol,'negative reduced gradient away from upper bound')
            dual_objective+=rc*hi; max_complementarity=max(max_complementarity,abs(rc*(value-hi)))
        else:max_stationarity=max(max_stationarity,abs(rc))
    require(abs(objective-dual_objective)<=1e-5+1e-7*max(abs(objective),abs(dual_objective)),'primal/dual objective mismatch')
    return dict(primal_feasible=True,dual_feasible=True,complete_finite_duals=True,
        objective_yuan=objective,dual_objective_yuan=dual_objective,
        max_free_stationarity_residual=max_stationarity,max_complementarity=max_complementarity,
        primal_count=len(values),dual_count=len(duals))


def _prices(pricing):
    m=pricing.model; audit=pricing.audit; prices={n:[] for n in m.N}; raw=[]
    for t in m.T:
        balance=float(m.dual[m.c_balance[t]])/.25
        families={}
        record=dict(quarter=t,balance_raw_rhs_dual=float(m.dual[m.c_balance[t]]),duration_h=.25,
            normalization='balance=raw/0.25; tau_upper=-raw_upper/0.25; tau_lower=-raw_lower/0.25',lines={},sections={})
        for family,ids,upper,lower in [('lines',m.L,m.c_line_upper,m.c_line_lower),('sections',m.S,m.c_section_upper,m.c_section_lower)]:
            families[family]={}
            for key in ids:
                up=float(m.dual[upper[key,t]]); low=float(m.dual[lower[key,t]])
                require(up<=0 and low<=0,'network <= row must have nonpositive solver dual')
                families[family][key]=(-up/.25,-low/.25)
                record[family][key]=dict(upper_raw_rhs_dual=up,lower_raw_rhs_dual=low,
                    positive_yuan_per_mwh=families[family][key][0],negative_yuan_per_mwh=families[family][key][1])
        raw.append(record)
        for n in m.N:
            terms={family:tuple(NetworkMultiplier(key,plus,minus,audit['line_ptdf' if family=='lines' else 'section_ptdf'][key][n])
                for key,(plus,minus) in families[family].items()) for family in ('lines','sections')}
            r=october_lmp_components(balance_yuan_per_mwh=balance,**terms,
                multiplier_basis='rule_normalized_yuan_per_mwh',source='genuine HiGHS independent October 8x15 research LP '+pricing.model_sha256)
            r.update(independent_model_solution_verified=True,network_coverage_verified=True)
            prices[n].append(r)
    return prices,raw


def solve_october_pricing_lp(pricing, *, time_limit_s=60.):
    audit=assert_october_pricing_structure(pricing)
    num(time_limit_s,'time limit',0); require(time_limit_s>0,'positive time limit required')
    options=highs_solver.validate_options(dict(time_limit=float(time_limit_s),threads=1,mip_rel_gap=.001,mip_abs_gap=0.))
    native=native_pricing_path_intact()
    solver=highs_solver.HighsSolver(options)
    report=dict(kind='independent_october_lp_research',status='unavailable',official_price=None,
        prices=None,published=False,settlement_cash_created=False,whole_day=False,
        model_sha256=pricing.model_sha256,input_sha256=pricing.inputs.input_sha256,
        identity=audit['identity'],diagnostic_probe=audit['diagnostic_probe'],research_only=True,
        operational_mapping_verified=False,source_authenticity_verified=False,native_call_verified=False,solve_evidence='native_attempt' if native else 'mock_or_replaced_call_path')
    if audit.get('pricing_dispatch_executable')is False:
        report.update(pricing_dispatch_executable=False,pricing_ramp_basis=audit['pricing_ramp_basis'],execution_confirmed=False,rolling_authority=False,
            original_ed_cost=audit['native_reference']['original_ed_cost'],price_model_cost_at_ed_mean=audit['native_reference']['price_model_cost_at_ed_mean'],aggregation_cost_gap=audit['native_reference']['aggregation_cost_gap'],
            ramp_domain_limits=audit['native_reference']['ramp_domain_limits'],slack_domain_limits=audit['native_reference']['slack_domain_limits'])
    if 'storage' in audit:report['storage_provenance']=audit['storage']['source_provenance']
    if 'native_reference' in audit:
        report['ed_reference_provenance']=audit['native_reference']
        report['actual_pricing_started_at']=datetime.now(timezone.utc).isoformat()
    try:
        solver.solve(pricing.model)
        assert_october_pricing_structure(pricing)
        native=native and native_pricing_path_intact()
        report.update(solver_status=solver.status,solver_version=solver.solver_version,native_settings=solver.native_settings,
            time_settings=solver.time_settings)
        require(native,'mock/replaced call path cannot issue native research prices')
        require(solver.status=='optimal' and solver.primal_available and solver.dual_available,'optimal LP with native primal and duals required')
        require(solver.solver_version=='1.15.1','this research receipt requires tested HiGHS 1.15.1')
        kkt=_audit_optimum(pricing.model,options)
        prices,raw=_prices(pricing)
        if audit.get('pricing_dispatch_executable')is False:report['pricing_optimum_cost']=kkt['objective_yuan']
        report.update(status='optimal_research',prices=prices,raw_dual_normalization=raw,kkt=kkt,
            objective_yuan=kkt['objective_yuan'],native_call_verified=True,
            derivative_scope='energy marginal with reserve demand and requirements held fixed; positive-reserve-linked demand is a separate diagnostic; independent negative-demand baseline stays fixed',
            dual_unique=False,source_code_hashes=audit['source_code_hashes'],source_hashes=audit['source_hashes'])
    except (ValueError,RuntimeError,KeyError,TypeError,OverflowError) as exc:
        report.update(status='unavailable',prices=None,error=str(exc),solver_status=solver.status,
            solver_version=solver.solver_version,native_call_verified=False)
    if 'native_reference' in audit:report['actual_pricing_completed_at']=datetime.now(timezone.utc).isoformat()
    result=OctoberPricingSolve(report['status'],canonical(report))
    _ISSUED[result]=(pricing,result.artifact,_snapshot(pricing.model),native)
    return result


def extract_october_pricing_research_result(pricing, result):
    require(type(result) is OctoberPricingSolve and result in _ISSUED,'genuine issued in-process receipt required; serialized/forged results rejected')
    original,artifact,snapshot,native=_ISSUED[result]
    require(original is pricing and result.artifact==artifact,'result/model/receipt identity mismatch')
    assert_october_pricing_structure(pricing)
    require(snapshot==_snapshot(pricing.model),'primal or dual changed after solve')
    report=result.report
    require(result.status==report['status'],'receipt status changed')
    if report['status']=='optimal_research':
        require(native and report['native_call_verified'] and native_pricing_path_intact(),'native receipt provenance changed')
        _audit_optimum(pricing.model,highs_solver.validate_options(dict(time_limit=60.)))
    return report


# Trace ordinary monkeypatches across local adapters as well as the shared backend.
# Like existing receipts this is not a sandbox against hostile in-process Python.
from . import gd_october_pricing_inputs as _inputs_module
from . import gd_october_pricing_model as _model_module
from . import gd_october_pricing_network as _network_module
from . import gd_october_pricing_rules as _rules_module
from . import gd_october_pricing_storage as _storage_module
from . import gd_october_pricing_native_bridge as _bridge_module
from . import gd_october_native_snapshot as _snapshot_module
from . import gd_october_pricing_thermal_v4 as _thermal_module
_OWN_HELPERS={name:globals()[name] for name in ('_snapshot','_audit_optimum','_prices','solve_october_pricing_lp','extract_october_pricing_research_result')}
_OTHER_HELPERS=tuple((module,name,getattr(module,name)) for module,names in (
    (_inputs_module,('_prepare','prepare_october_pricing_inputs','assert_pricing_inputs_current')),
    (_model_module,('build_october_pricing_lp','assert_october_pricing_structure','_signature','code_hashes')),
    (_snapshot_module,tuple(_snapshot_module._HELPERS)+('snapshot_path_intact',)),
    (_thermal_module,('derive_thermal_pricing_rows','validate_thermal_pricing_v4','_sources','_same','_constant','_integral')),
    (_bridge_module,('_materialize_snapshot','prepare_october_snapshot_pricing_inputs','_storage_sources_explicit','_materialize','_storage_sources','assert_native_pricing_binding','prepare_october_native_pricing_inputs','is_issued_native_reference','native_bridge_path_intact','snapshot_bridge_path_intact','_quarters','_zero','_same')),
    (_network_module,('dc_ptdf','normalize_topology_ptdf')),(_rules_module,('october_lmp_components',)),
    (_storage_module,('validate_storage_pricing','attach_pricing_storage','signed_magnitude_band','_header','_ids','_same','_point_bounds','_terminal','_bids'))) for name in names)

def native_pricing_path_intact():
    return (highs_solver.native_solver_path_intact() and
        assert_october_pricing_structure is _ORIGINAL_ASSERT is _model_module.assert_october_pricing_structure and
        october_lmp_components is _ORIGINAL_FORMULA is _rules_module.october_lmp_components and
        _model_module.assert_pricing_inputs_current is _inputs_module.assert_pricing_inputs_current and
        _model_module.dc_ptdf is _network_module.dc_ptdf and
        _model_module.normalize_topology_ptdf is _network_module.normalize_topology_ptdf and
        all(globals()[name] is original for name,original in _OWN_HELPERS.items()) and
        all(getattr(module,name) is original for module,name,original in _OTHER_HELPERS))
