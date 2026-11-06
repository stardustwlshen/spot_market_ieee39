"""Read-only stage snapshots. These never fix variables or change objectives."""
from __future__ import annotations
import pyomo.environ as pyo


def capture(m, stage, info=None):
    md=m._md;rows=[];offset=m._case.t0;enabled=getattr(m,'_agc',{}).get('enabled',False)
    val=lambda v:float(pyo.value(v))
    for t in m.T:
        def add(uid,kind,power,on=None,**kw):
            q=val(m.q_agc[uid,t]) if enabled and uid in m.AGC else None
            rows.append(dict(period=offset+int(t),unit=uid,type=kind,P_mw=power,on=on,q_reg_mw=q,E_mwh=None,V_up_1e4m3=None,**kw))
        for uid,d in md.thermal.items():add(uid,d['type'],val(m.P[uid,t]),int(round(val(m.u[uid,t]))))
        for uid,d in md.ren.items():add(uid,d['type'],val(m.Pw[uid,t]))
        for uid in md.hydro_units:add(uid,'hydro',val(m.Ph[uid,t]),int(sum(val(m.delta[j,z,t]) for j,z in m.JZ if j==uid)>0))
        for uid in md.storage:
            add(uid,'storage',val(m.Pd[uid,t]-m.Pc[uid,t]),int(round(val(m.u_dis[uid,t]+m.u_ch[uid,t]))))
            rows[-1].update(E_mwh=val(m.E[uid,t]),P_dis_mw=val(m.Pd[uid,t]),P_ch_mw=val(m.Pc[uid,t]))
        for uid in md.pumped:
            add(uid,'pumped',val(m.Pg[uid,t]-m.Pp[uid,t]),int(round(val(m.xg[uid,t]+m.xp[uid,t]))))
            rows[-1].update(V_up_1e4m3=val(m.Vu[uid,t]),P_gen_mw=val(m.Pg[uid,t]),P_pump_mw=val(m.Pp[uid,t]))
    summary=dict(info or {})
    # Do not turn the auxiliary objective (score) into actual cost/payment.
    summary['stage']=stage
    summary['status']='正式SCED（RT仅窗口首段执行）' if stage=='energy_sced' else '预计划/辅助筛选（不执行、不结算）'
    summary['objective_kind']='regulation_score_only_not_payment' if stage=='agc_auxiliary' else 'energy_objective_including_penalties'
    market_stage = getattr(m._case, 'stage', 'DA')
    auxiliary = bool(m._case.cfg.mode.get('auxiliary_pricing', {}).get('enabled', False))
    selected = stage == ('auxiliary_pricing' if auxiliary else 'energy_sced')
    summary.update(market_stage=market_stage, window_start=offset, window_periods=len(m.T),
                   dt_h=m._case.cfg.dt, quantities_execute=False,
                   quantity_role=('RT_first_period_only' if market_stage == 'RT' else
                                  'DA_award_plan' if market_stage == 'DA' else 'ID_reference_plan') if stage == 'energy_sced' else 'not_executed',
                   prices_are_market_prices=bool(selected and market_stage != 'ID'),
                   selected_price_source=selected,
                   price_role=('indicative_not_settled' if market_stage == 'ID' else
                               'selected_settlement_price_rule') if selected else 'not_selected_for_settlement')
    if market_stage == 'ID':
        summary['status'] = '日内参考计划/指示价（不执行、不结算）'
    prices = []
    if stage in ('energy_sced', 'auxiliary_pricing'):
        from .scuc import extract_prices
        raw = extract_prices(m)['lmp']
        lim = m._case.cfg.mode['price_limits']
        prices = [dict(period=offset+int(t), bus=bus, lmp_raw=float(raw[i, int(t)]),
                       lmp_clamped=float(min(lim['clear_max'], max(lim['clear_min'], raw[i, int(t)]))))
                  for i, bus in enumerate(m._case.net.buses) for t in m.T]
    water = [dict(period=offset+int(t), station=h, V_1e4m3=val(m.V[h,t]),
                  Qout_m3s=val(d['water_rate']*sum(m.Ph[j,t] for j in d['units'])+m.Qspill[h,t]))
             for h,d in md.hydro.items() for t in m.T]
    from .scuc import integer_components
    integer_values = [dict(variable=name, index=list(index) if isinstance(index, tuple) else index,
                           value=val(v), fixed=bool(v.fixed))
                      for name, component in integer_components(m) for index, v in component.items()]
    from .asset_reachability import diagnostics
    summary['asset_reachability']=diagnostics(m)
    return {'units':rows,'summary':summary,'integer_values':integer_values,'prices':prices,'hydro_states':water}
