"""Predeclared R21 PRICE-only terminal research parameters, never declarations.

Official text confirms initial/terminal equality structure, but does not say
that each rolling PRICE endpoint is the archived DA physical SOC. This policy
keeps equality and authentic initial state, carries the source DA energy change
relative to it, and preserves the original absolute D96 anchor when covered.
No clipping, relaxation, state reset, native solve or formal authority exists.
"""
from decimal import Decimal, localcontext
from .gd_r15_da_rt_bridge import require, finite, digest, stamp
from .gd_r17_midnight_contract import TARGETS

TOL=1e-8
POLICY=dict(schema='gd.research.r21.PRICE_terminal_policy.v1',
    scope='independent_PRICE_only_RT_unchanged',
    before_D96='Efin=authenticated_E0+(original_DA_Eend-original_DA_Estart)',
    covers_D96='Efin=original_absolute_D96_target+(original_DA_Eend-original_DA_Eat_D96)',
    D96_absolute_targets=TARGETS.copy(),
    initial_state='unchanged_same_live_cutoff_available_receipt_plus_issued_RT_projection',
    no_state_reset=True,no_parameter_clipping=True,no_implicit_relaxation=True,
    original_DA_power_bands_unchanged=True,original_RT_unchanged=True,
    preserve_SOC_dynamics_hard_bounds_and_dated_internal_usage=True,
    preserve_D96_anchor=True,preflight='hard target bounds and necessary ramp/mode/SOC reachability, not sufficiency',
    formal_declared_terminal_parameters=None,formal_strict_supported=False,
    meaning='globally_predeclared_research_parameter_mapping_not_an_official_resource_submission',
    official_structure='regional3.4.4.11 E0=Eini and ET=Efin; mapping unresolved',
    official_market=False,formal_bill=None,native_calls_authorized=0)


def _decimal_formula(anchor,end,start):
    with localcontext() as context:
        context.prec=50
        value=Decimal(str(anchor))+(Decimal(str(end))-Decimal(str(start)))
        return float(value),str(value)


def _necessary_reachability(inp,r,anchor_index,anchor_target):
    """Outer reachable intervals. Empty/excluded targets prove impossibility.

    Correlation between power and SOC is relaxed in this check, so passing is
    NOT a feasible witness. Replacing an interval by the D96 target conditions
    this mathematical check on a required boundary; it never edits real state.
    """
    u=inp['resources'][r];state=inp['projection']['state']['batteries'][r]
    emin,emax=map(finite,(u['energy_min_mwh'],u['energy_max_mwh']))
    elo=ehi=finite(state['energy_mwh']);plo=phi=finite(state['power_mw'])
    dates_min={d:finite(v) for d,v in state['daily_used_internal_mwh'].items()}
    trace=[];anchor_range=None
    for j,row in enumerate(u['rows']):
        low=max(finite(row['min_mw']),plo-finite(row['ramp_down_mw_h'])/12)
        high=min(finite(row['max_mw']),phi+finite(row['ramp_up_mw_h'])/12)
        require(low<=high+TOL,'PRICE terminal preflight: sourced mode/ramp interval unreachable')
        mode=row['mode'];require(mode in ('charge','discharge','idle'),'sourced battery mode required')
        coef=finite(u['eta_charge']) if mode=='charge' else 1/finite(u['eta_discharge'])
        nextlo=max(emin,elo-coef*high/12);nexthi=min(emax,ehi-coef*low/12)
        require(nextlo<=nexthi+TOL,'PRICE terminal preflight: SOC hard-bound interval unreachable')
        date=stamp(inp['da_cells'][j]['start']).date().isoformat()
        min_used=(-coef*high/12 if mode=='charge' else coef*low/12 if mode=='discharge' else 0.)
        dates_min[date]=dates_min.get(date,0.)+max(0.,min_used)
        require(date in u['cycle_budget'] and dates_min[date]<=u['cycle_budget'][date]['limit_internal_mwh']+TOL,
                'PRICE terminal preflight: necessary dated internal usage exceeds original limit')
        row_trace=dict(physical_cell=j,mode=mode,power_lower_mw=low,power_upper_mw=high,
            energy_lower_mwh=nextlo,energy_upper_mwh=nexthi)
        if j+1==anchor_index:
            anchor_range=dict(local_energy_index=anchor_index,lower_mwh=nextlo,upper_mwh=nexthi,target_mwh=anchor_target)
            require(nextlo-TOL<=anchor_target<=nexthi+TOL,'PRICE terminal preflight: original D96 hard anchor unreachable')
            # Conditional range only; the immutable input state is not modified.
            nextlo=nexthi=anchor_target
            row_trace['conditioned_on_original_D96_anchor_mwh']=anchor_target
        trace.append(row_trace);plo,phi=low,high;elo,ehi=nextlo,nexthi
    return dict(endpoint_outer_lower_mwh=elo,endpoint_outer_upper_mwh=ehi,
        D96_anchor_outer_range=anchor_range,per_cell=trace,
        inherited_and_necessary_minimum_daily_internal_mwh=dates_min,
        sufficient_feasibility_proof=False,actual_state_changed=False)


def terminal_trace(inp,formal_strict=False,declared_parameters=None):
    """Return complete, deterministic PRICE terminal parameter provenance."""
    require(type(formal_strict) is bool,'explicit formal-strict mode required')
    require(not formal_strict,'formal strict blocked: no authenticated official declared terminal parameter contract; research Efin cannot substitute')
    require(declared_parameters is None,'unverified declared terminal parameters cannot override research policy')
    require(inp['midnight_targets']==TARGETS,'original D96 target inventory changed')
    w=inp['window'];i=w['index'];require(type(i) is int and 0<=i<96,'D-day PRICE window index required')
    k=w['midnight_local_energy_index'];expected=288-3*i if 3*i<288<=3*i+24 else None
    require(k==expected,'D96 energy boundary index changed')
    require(len(inp['da_cells'])==24,'original24-cell physical reference required')
    result={}
    for r,u in inp['resources'].items():
        if u['kind']!='battery':continue
        initial=finite(inp['projection']['state']['batteries'][r]['energy_mwh'])
        source_start=finite(inp['da_cells'][0]['batteries'][r]['energy_begin_mwh'])
        source_end=finite(inp['da_cells'][-1]['batteries'][r]['energy_end_mwh'])
        require(abs(source_end-finite(u['terminal_energy_mwh']))<=TOL,'original DA physical endpoint changed')
        anchor=None
        if k is None:
            effective,exact=_decimal_formula(initial,source_end,source_start)
            formula=POLICY['before_D96'];anchor_index=None;anchor_target=None
        else:
            anchor_source=finite(inp['da_cells'][k-1]['batteries'][r]['energy_end_mwh'])
            anchor_target=finite(inp['midnight_targets'][r]);anchor_index=k
            require(abs(anchor_source-anchor_target)<=TOL,'source D96 anchor differs from original absolute target')
            effective,exact=_decimal_formula(anchor_target,source_end,anchor_source)
            formula=POLICY['covers_D96']
            anchor=dict(local_energy_index=k,global_energy_index=288,absolute_target_mwh=anchor_target,
                original_DA_anchor_mwh=anchor_source,original_DA_after_anchor_change_mwh=source_end-anchor_source)
        emin,emax=map(finite,(u['energy_min_mwh'],u['energy_max_mwh']))
        require(emin-TOL<=initial<=emax+TOL,'PRICE initial SOC outside original hard bounds')
        require(emin-TOL<=effective<=emax+TOL,'translated PRICE terminal outside original SOC hard bounds; no clipping')
        reachable=_necessary_reachability(inp,r,anchor_index,anchor_target)
        require(reachable['endpoint_outer_lower_mwh']-TOL<=effective<=reachable['endpoint_outer_upper_mwh']+TOL,
                'translated PRICE terminal outside necessary reachable interval; no repair')
        result[r]=dict(schema='gd.research.r21.PRICE_terminal_trace.v1',policy_sha256=digest(POLICY),
            window_index=i,window_start=w['start'],window_end=w['horizon_end'],formula=formula,
            actual_causal_initial_mwh=initial,original_DA_initial_mwh=source_start,original_DA_terminal_mwh=source_end,
            original_DA_net_change_mwh=source_end-source_start,inherited_actual_minus_DA_offset_mwh=initial-source_start,
            D96_anchor=anchor,effective_terminal_mwh=effective,effective_terminal_decimal=exact,
            SOC_hard_lower_mwh=emin,SOC_hard_upper_mwh=emax,necessary_reachability=reachable,
            clipped=False,actual_state_changed=False,RT_terminal_changed=False,DA_power_bands_changed=False,
            official_submitted_parameter=None,formal_strict=False,policy_is_research=True)
    require(set(result)==set(inp['technical']['batteries']),'complete battery terminal inventory required')
    return result
