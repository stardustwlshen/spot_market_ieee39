"""Independent reserve audit from full-precision evidence; no model imports/solve.
Usage: python audit_reserve_windows.py OUTPUT/reserve_window_evidence.json
"""
import json,math,sys
from pathlib import Path


def audit(payload,tol=1e-5):
    out=dict(windows=0,resource_periods=0,qualified_resource_periods=0,positive_reserve_resource_periods=0,
             max_violation=0.,max_export_difference=0.,minimum_selected_duration_margin_mwh=None,minimum_selected_duration_margin_1e4m3=None,
             full_activation_risk_resource_periods=0,minimum_full_activation_margin_mwh=None,minimum_full_activation_margin_1e4m3=None,
             rt_executed_reserve_up_short_max_mw=0.,rt_executed_reserve_down_short_max_mw=0.,
             hydro_water_checked_resource_periods=0,hydro_water_legacy_resource_periods=0,
             hydro_water_violation_count=0,hydro_water_check_passed=None,
             hydro_outflow_checked_resource_periods=0,hydro_outflow_unconfigured_resource_periods=0,
             hydro_outflow_legacy_resource_periods=0,hydro_outflow_violation_count=0,
             hydro_outflow_check_passed=None,violations=[])
    def number(v):
        if isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v):raise ValueError(f'Invalid numeric evidence {v!r}')
        return float(v)
    def margin(v,label):
        v=number(v);out['max_violation']=max(out['max_violation'],max(0.,-v))
        if v < -tol:out['violations'].append(dict(check=label,margin=v))
    def equal(a,b,label):
        d=abs(number(a)-number(b));out['max_export_difference']=max(out['max_export_difference'],d)
        if d>tol:out['violations'].append(dict(check=label,difference=d))
    def selected(v,unit):
        v=number(v);key='minimum_selected_duration_margin_'+unit;old=out[key];out[key]=v if old is None else min(old,v)
    stages=[('DA',payload['da'])]+[(f'ID{i}',e) for i,e in enumerate(payload.get('id_windows',[]))]+[(f'RT{i}',e) for i,e in enumerate(payload['rt_windows'])]
    for stage,e in stages:
        out['windows']+=1;enabled=e['storage_reserve_enabled'];down_enabled=e['down_enabled']
        rows=e['resources'];catalog=e['resource_catalog'];T=int(e['periods']);start=int(e['window_start'])
        new_water=e.get('evidence_version') is not None
        hydro_expected='hydro' in e['providers'] and any(r.get('type')=='hydro' for r in rows)
        if new_water:
            if e['evidence_version'] not in (2,3) or e.get('hydro_reserve_water_formulation')!='period_end_stock_incremental_up_water_v1':
                raise ValueError('Missing/unknown hydro reserve water evidence version or formulation')
            if not isinstance(e.get('hydro_reserve_water_enabled'),bool) or e['hydro_reserve_water_enabled']!=hydro_expected:
                raise ValueError('Hydro reserve water participation is inconsistent with providers/resources')
        elif ('hydro_reserve_water_formulation' in e or 'hydro_reserve_water_enabled' in e
              or any('hydro_reserve_water_checked' in row for row in rows)):
            raise ValueError('Hydro reserve water evidence is missing its schema version')
        new_outflow=e.get('evidence_version')==3
        outflow_expected=hydro_expected and any(r.get('type')=='hydro' and r.get('qout_max_m3s') is not None for r in rows)
        if new_outflow:
            if (e.get('hydro_reserve_outflow_formulation')!='generation_flow_with_reducible_spill_v1'
                    or e.get('hydro_reserve_outflow_enabled') is not outflow_expected):
                raise ValueError('Missing/inconsistent hydro reserve outflow policy')
        elif ('hydro_reserve_outflow_formulation' in e or 'hydro_reserve_outflow_enabled' in e
              or any('hydro_reserve_outflow_checked' in r for r in rows)):
            raise ValueError('Hydro reserve outflow evidence cannot downgrade its schema version')
        hydro_water_enabled=new_water and hydro_expected
        H=number(e['T_dur_h']) if enabled or hydro_water_enabled else None
        if H is not None and H<=0:raise ValueError('Nonpositive duration')
        pairs=[(r['unit'],r['relative_period']) for r in rows]
        if len(pairs)!=len(set(pairs)) or set(pairs)!={(u,t) for u in catalog for t in range(T)}:raise ValueError('Incomplete/duplicate resource grid')
        system={x['relative_period']:x for x in e['system']}
        if set(system)!=set(range(T)) or len(e['system'])!=T:raise ValueError('Incomplete/duplicate system grid')
        for t in range(T):
            current=[r for r in rows if r['relative_period']==t];up=dn=0.
            for r in current:
                out['resource_periods']+=1;uid=r['unit'];typ=r['type'];label=f'{stage}/{t}/{uid}'
                if r['period']!=start+t:raise ValueError('Wrong absolute period')
                ru,rd,P=map(number,(r['r_up'],r['r_dn'],r['P_mw']))
                margin(ru,label+'/ru_nonnegative');margin(rd,label+'/rd_nonnegative')
                if not down_enabled:equal(rd,0.,label+'/down_disabled')
                if typ=='storage':eligible=enabled
                elif typ=='pumped':eligible=enabled and r['mode']=='generating' and number(r['n_gen'])>0
                else:eligible=typ in e['providers'] and r['mode']=='online'
                if r['reserve_eligible']!=eligible:raise ValueError('Eligibility misreported')
                if eligible:
                    up+=ru;dn+=rd
                    if ru+rd>tol:out['positive_reserve_resource_periods']+=1
                if typ not in ('storage','pumped'):
                    if eligible:
                        margin(number(r['pmax_mw'])-P-ru,label+'/power_up');margin(P-number(r['pmin_mw'])-rd,label+'/power_down')
                    if typ=='hydro':
                        if new_outflow:
                            before_flow=len(out['violations'])
                            rate=number(r['water_rate_m3s_per_mw'])
                            if rate<=0:raise ValueError('Nonpositive hydro water rate')
                            spill=number(r['Qspill_base_m3s']);eco=number(r['qeco_m3s'])
                            qmax=r['qout_max_m3s'];checked=hydro_expected and qmax is not None
                            status='checked' if checked else 'not_configured' if qmax is None else 'not_participating'
                            if r.get('hydro_reserve_outflow_checked') is not checked or r.get('hydro_reserve_outflow_status')!=status:
                                raise ValueError('Hydro reserve outflow check status is inconsistent')
                            margin(spill,label+'/spill_nonnegative');margin(eco,label+'/ecology_nonnegative')
                            qgen=rate*P;base=qgen+spill;active_gen=qgen+rate*ru
                            active_spill=max(0.,spill-rate*ru);active_out=active_gen+active_spill
                            for key,value in [('Qgen_base_m3s',qgen),('Qout_base_m3s',base),
                                    ('reserve_up_activation_generation_m3s',active_gen),
                                    ('reserve_up_activation_spill_m3s',active_spill),
                                    ('reserve_up_activation_outflow_m3s',active_out),
                                    ('reserve_up_ecological_margin_m3s',active_out-eco)]:
                                equal(r[key],value,label+'/export_'+key)
                            margin(base-eco,label+'/base_ecological_flow')
                            margin(active_out-eco,label+'/activation_ecological_flow')
                            if qmax is not None:
                                qmax=number(qmax);margin(qmax,label+'/outflow_max_nonnegative')
                                margin(qmax-base,label+'/baseline_outflow_limit')
                            if checked:
                                margin(qmax-active_out,label+'/activation_outflow_limit')
                                equal(r['reserve_up_outflow_margin_m3s'],qmax-active_out,label+'/export_outflow_margin')
                                out['hydro_outflow_checked_resource_periods']+=1
                            else:
                                if r['reserve_up_outflow_margin_m3s'] is not None:
                                    raise ValueError('Unconfigured/inactive outflow margin must be N/A')
                                if hydro_expected and qmax is None:
                                    out['hydro_outflow_unconfigured_resource_periods']+=1
                            out['hydro_outflow_violation_count']+=len(out['violations'])-before_flow
                        elif hydro_expected:
                            out['hydro_outflow_legacy_resource_periods']+=1
                        if hydro_water_enabled:
                            before=len(out['violations'])
                            if r.get('hydro_reserve_water_checked') is not True:
                                raise ValueError('Missing hydro reserve water check evidence')
                            equal(r['T_dur_h'],H,label+'/hydro_duration')
                            rate=number(r['water_rate_m3s_per_mw'])
                            if rate<=0:raise ValueError('Nonpositive hydro water rate')
                            lo=number(r['V_min_1e4m3']);hi=number(r['V_max_1e4m3'])
                            v0=number(r['V_start_1e4m3']);v1=number(r['V_end_1e4m3'])
                            margin(hi-lo,label+'/hydro_volume_bounds')
                            for stock in (v0,v1):
                                margin(stock-lo,label+'/hydro_stock_lower');margin(hi-stock,label+'/hydro_stock_upper')
                            available=v1-lo;required=.36*rate*H*ru;remaining=available-required
                            margin(remaining,label+'/hydro_incremental_up_water')
                            equal(r['reserve_available_water_1e4m3'],available,label+'/export_hydro_available_water')
                            equal(r['reserve_incremental_water_up_1e4m3'],required,label+'/export_hydro_required_water')
                            equal(r['reserve_duration_up_margin_1e4m3'],remaining,label+'/export_hydro_water_margin')
                            equal(r['reserve_duration_lower_margin_1e4m3'],remaining,label+'/export_hydro_water_lower_margin')
                            if r['reserve_duration_down_margin_1e4m3'] is not None:
                                raise ValueError('Hydro downward reserve water margin must remain N/A')
                            out['hydro_water_checked_resource_periods']+=1
                            out['hydro_water_violation_count']+=len(out['violations'])-before
                            selected(remaining,'1e4m3')
                        elif not new_water and hydro_expected:
                            out['hydro_water_legacy_resource_periods']+=1
                        elif new_water and r.get('hydro_reserve_water_checked') is not False:
                            raise ValueError('Disabled hydro reserve water must be explicitly unchecked')
                    continue
                if e.get('formulation') in ('incremental_current_stock','period_end_stock'):
                    end_stock=e.get('formulation')=='period_end_stock'
                    equal(r['reserve_qualified'],int(eligible),label+'/derived_eligibility')
                    if eligible:out['qualified_resource_periods']+=1
                    if not enabled:
                        equal(ru+rd,0.,label+'/off_zero')
                        continue
                    if typ=='storage':
                        cap=number(r['pmax_mw']);eta_c=number(r['eta_c']);eta_d=number(r['eta_d'])
                        if not 0<eta_c<=1 or not 0<eta_d<=1:raise ValueError('Efficiency outside (0,1]')
                        equal(P,number(r['P_dis_mw'])-number(r['P_ch_mw']),label+'/net_power')
                        margin(cap-P-ru,label+'/power_up');margin(cap+P-rd,label+'/power_down')
                        lo=number(r['E_min_mwh']);hi=number(r['E_max_mwh']);stock=number(r['E_end_mwh' if end_stock else 'E_start_mwh'])
                        for endpoint in (('end',) if t==0 else ('start','end')):
                            v=number(r[f'E_{endpoint}_mwh']);margin(v-lo,label+'/stock_lower');margin(hi-v,label+'/stock_upper')
                        u=max(0.,stock-lo)-H*ru/eta_d;d=max(0.,hi-stock)-H*rd*eta_c;unit='mwh'
                        s0=number(r['E_start_mwh']);end=number(r['E_end_mwh']);fu=min(s0,end)-lo-H*(P+ru)/eta_d  # 全激活风险始终用起止两端
                        fd=hi-max(s0,end)+H*eta_c*(P-rd if down_enabled else P+ru)
                    else:
                        pg,pp=number(r['P_gen_mw']),number(r['P_pump_mw']);n=number(r['n_gen'])
                        pmax,pmin=number(r['unit_gen_max_mw']),number(r['unit_gen_min_mw']);eg=number(r['e_gen_mwh_per_1e4m3'])
                        if eg<=0:raise ValueError('Nonpositive generation conversion')
                        equal(P,pg-pp,label+'/net_power')
                        margin(n*pmax-pg-ru,label+'/power_up');margin(pg-n*pmin-rd,label+'/power_down')
                        if not eligible:equal(ru+rd,0.,label+'/non_generating_zero')
                        lo=number(r['V_min_1e4m3']);hi=number(r['V_max_1e4m3']);stock=number(r['V_end_1e4m3' if end_stock else 'V_start_1e4m3'])
                        for endpoint in (('end',) if t==0 else ('start','end')):
                            v=number(r[f'V_{endpoint}_1e4m3']);margin(v-lo,label+'/stock_lower');margin(hi-v,label+'/stock_upper')
                        u=max(0.,stock-lo)-H*ru/eg;d=max(0.,hi-stock)-H*rd/eg;unit='1e4m3'
                        s0=number(r['V_start_1e4m3']);end=number(r['V_end_1e4m3']);fu=min(s0,end)-lo-H*(pg+ru)/eg
                        fd=hi-max(s0,end)+H*(pg-rd if down_enabled else pg+ru)/eg
                    margin(u,label+'/incremental_up')
                    if down_enabled:margin(d,label+'/incremental_down')
                    if eligible:
                        equal(r['full_activation_up_margin_'+unit],fu,label+'/export_full_activation_up')
                        equal(r['full_activation_upper_margin_'+unit],fd,label+'/export_full_activation_upper')
                        if ru+rd>tol:
                            key='minimum_full_activation_margin_'+unit;old=out[key]
                            out[key]=min(fu,fd) if old is None else min(old,fu,fd)
                            if min(fu,fd)<-tol:out['full_activation_risk_resource_periods']+=1
                        equal(r['reserve_duration_up_margin_'+unit],u,label+'/export_incremental_up')
                        equal(r['reserve_duration_lower_margin_'+unit],u,label+'/export_incremental_lower')
                        selected(min(u,d) if down_enabled else u,unit)
                        if down_enabled:
                            equal(r['reserve_duration_down_margin_'+unit],d,label+'/export_incremental_down')
                            equal(r['reserve_duration_upper_margin_'+unit],d,label+'/export_incremental_upper')
                        elif r['reserve_duration_down_margin_'+unit] is not None:
                            raise ValueError('Disabled down reserve margin must be N/A')
                    elif r['reserve_duration_up_margin_'+unit] is not None or r['reserve_duration_down_margin_'+unit] is not None:
                        raise ValueError('Ineligible reserve margins must be N/A')
                    continue
                z=number(r['reserve_qualified'])
                if z not in (0.,1.):raise ValueError('Qualification is not binary')
                if not enabled:
                    equal(ru+rd,0.,label+'/off_zero');equal(z,0.,label+'/off_qualification')
                    continue
                if z:out['qualified_resource_periods']+=1
                if ru+rd>tol and not z:margin(-ru-rd,label+'/positive_reserve_requires_z')
                if typ=='storage':
                    cap=number(r['pmax_mw']);eta_c=number(r['eta_c']);eta_d=number(r['eta_d'])
                    if not 0<eta_c<=1 or not 0<eta_d<=1:raise ValueError('Efficiency outside (0,1]')
                    equal(P,number(r['P_dis_mw'])-number(r['P_ch_mw']),label+'/net_power')
                    margin(cap-P-ru,label+'/power_up');margin(cap+P-rd,label+'/power_down')
                    margin(2*cap*z-ru-rd,label+'/qualification_gate')
                    mup=H*cap/eta_d;mdn=H*eta_c*cap
                    equal(r['reserve_M_up_mwh'],mup,label+'/tight_Mup');equal(r['reserve_M_down_mwh'],mdn,label+'/tight_Mdn')
                    for endpoint in ('start','end'):
                        E=number(r[f'E_{endpoint}_mwh']);lo=number(r['E_min_mwh']);hi=number(r['E_max_mwh'])
                        margin(E-lo,label+'/stock_lower');margin(hi-E,label+'/stock_upper')
                        upper_power=P-rd if down_enabled else P+ru
                        u=E-lo-H*(P+ru)/eta_d;d=hi-E+H*eta_c*upper_power
                        margin(u+mup*(1-z),label+'/'+endpoint+'/conditional_up');margin(d+mdn*(1-z),label+'/'+endpoint+'/conditional_down')
                        if z:margin(u,label+'/'+endpoint+'/duration_up');margin(d,label+'/'+endpoint+'/duration_down');selected(min(u,d),'mwh')
                    if not z and (r['reserve_duration_up_margin_mwh'] is not None or r['reserve_duration_down_margin_mwh'] is not None):raise ValueError('Unqualified duration must be N/A')
                else:
                    pg,pp=number(r['P_gen_mw']),number(r['P_pump_mw']);n=number(r['n_gen']);N=number(r['total_units'])
                    pmax,pmin=number(r['unit_gen_max_mw']),number(r['unit_gen_min_mw']);eg=number(r['e_gen_mwh_per_1e4m3'])
                    equal(P,pg-pp,label+'/net_power');margin(number(r['x_gen'])-z,label+'/gen_qualification')
                    margin(n*pmax-pg-ru,label+'/power_up');margin(pg-n*pmin-rd,label+'/power_down')
                    margin(N*(pmax-pmin)*z-ru-rd,label+'/qualification_gate')
                    if not eligible:equal(ru+rd,0.,label+'/non_generating_zero')
                    mup=H*N*pmax/eg;equal(r['reserve_M_up_1e4m3'],mup,label+'/tight_Mup')
                    for endpoint in ('start','end'):
                        V=number(r[f'V_{endpoint}_1e4m3']);lo=number(r['V_min_1e4m3']);hi=number(r['V_max_1e4m3'])
                        margin(V-lo,label+'/stock_lower');margin(hi-V,label+'/stock_upper')
                        upper_power=pg-rd if down_enabled else pg+ru
                        u=V-lo-H*(pg+ru)/eg;d=hi-V+H*upper_power/eg
                        margin(u+mup*(1-z),label+'/'+endpoint+'/conditional_up');margin(d,label+'/'+endpoint+'/volume_upper')
                        if z:margin(u,label+'/'+endpoint+'/duration_up');selected(min(u,d),'1e4m3')
                    if not z and (r['reserve_duration_up_margin_1e4m3'] is not None or r['reserve_duration_down_margin_1e4m3'] is not None):raise ValueError('Unqualified duration must be N/A')
            sr=system[t];params=e['reserve_requirement_parameters']
            if not down_enabled:
                equal(sr['R_dn_req'],0.,stage+'/down_requirement_disabled');equal(sr['R_dn_short'],0.,stage+'/down_short_disabled')
            credit=number(params['mu_RE'])*(number(sr['renewable_available_mw'])-number(sr['renewable_dispatch_mw']))
            equal(sr['R_up_re_credit_mw'],credit,stage+'/renewable_credit');up+=credit
            equal(sr['R_up_supply'],up,stage+'/supply_up');equal(sr['R_dn_supply'],dn,stage+'/supply_down')
            for direction,supply in [('up',up),('dn',dn)]:
                shortage=number(sr[f'R_{direction}_short']);req=number(sr[f'R_{direction}_req'])
                margin(shortage,stage+'/shortage_nonnegative');margin(supply+shortage-req,stage+'/requirement_'+direction)
                equal(sr[f'R_{direction}_balance_mw'],supply+shortage-req,stage+'/balance_'+direction)
            if e.get('contingency_mw') is not None:
                load=number(sr['load_mw']);renewable=number(sr['renewable_available_mw'])
                equal(sr['R_up_req'],number(e['contingency_mw'])+params['rho_L']*load+params['rho_RE']*renewable,stage+'/unchanged_requirement_up')
                equal(sr['R_dn_req'],params['rho_L_dn']*load+params['rho_RE_dn']*renewable if down_enabled else 0.,stage+'/requirement_down')
            if stage.startswith('RT') and t==0:
                out['rt_executed_reserve_up_short_max_mw']=max(out['rt_executed_reserve_up_short_max_mw'],sr['R_up_short'])
                out['rt_executed_reserve_down_short_max_mw']=max(out['rt_executed_reserve_down_short_max_mw'],sr['R_dn_short'])
    if out['hydro_water_legacy_resource_periods']:
        out['hydro_water_check_scope']='legacy_capacity_only_not_water_verified'
    elif out['hydro_water_checked_resource_periods']:
        out['hydro_water_check_passed']=out['hydro_water_violation_count']==0
        out['hydro_water_check_scope']='period_end_stock_incremental_up_water_v1'
    else:
        out['hydro_water_check_scope']='not_applicable_no_hydro_reserve_participation'
    if out['hydro_outflow_legacy_resource_periods']:
        out['hydro_outflow_check_scope']='legacy_outflow_activation_not_verified'
    elif out['hydro_outflow_checked_resource_periods']:
        out['hydro_outflow_check_passed']=out['hydro_outflow_violation_count']==0
        out['hydro_outflow_check_scope']='generation_flow_with_reducible_spill_v1'
    elif out['hydro_outflow_unconfigured_resource_periods']:
        out['hydro_outflow_check_scope']='not_configured_no_hydro_outflow_limit'
    else:
        out['hydro_outflow_check_scope']='not_applicable_no_hydro_reserve_participation'
    out['passed']=not out['violations']
    return out


if __name__=='__main__':
    result=audit(json.loads(Path(sys.argv[1]).read_text()))
    print(json.dumps(result,ensure_ascii=False,indent=2))
    raise SystemExit(0 if result['passed'] else 1)
