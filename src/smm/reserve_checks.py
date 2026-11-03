"""Independent reserve audit from full-precision evidence; no model imports/solve.
Usage: python audit_reserve_windows.py OUTPUT/reserve_window_evidence.json
"""
import json,math,sys
from pathlib import Path


def audit(payload,tol=1e-5):
    out=dict(windows=0,resource_periods=0,qualified_resource_periods=0,positive_reserve_resource_periods=0,
             max_violation=0.,max_export_difference=0.,minimum_selected_duration_margin_mwh=None,minimum_selected_duration_margin_1e4m3=None,
             full_activation_risk_resource_periods=0,minimum_full_activation_margin_mwh=None,minimum_full_activation_margin_1e4m3=None,
             rt_executed_reserve_up_short_max_mw=0.,rt_executed_reserve_down_short_max_mw=0.,violations=[])
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
    stages=[('DA',payload['da'])]+[(f'RT{i}',e) for i,e in enumerate(payload['rt_windows'])]
    for stage,e in stages:
        out['windows']+=1;enabled=e['storage_reserve_enabled'];down_enabled=e['down_enabled'];H=number(e['T_dur_h']) if enabled else None
        if enabled and H<=0:raise ValueError('Nonpositive duration')
        rows=e['resources'];catalog=e['resource_catalog'];T=int(e['periods']);start=int(e['window_start'])
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
                    continue
                if e.get('formulation')=='incremental_current_stock':
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
                        lo=number(r['E_min_mwh']);hi=number(r['E_max_mwh']);stock=number(r['E_start_mwh'])
                        for endpoint in (('end',) if t==0 else ('start','end')):
                            v=number(r[f'E_{endpoint}_mwh']);margin(v-lo,label+'/stock_lower');margin(hi-v,label+'/stock_upper')
                        u=max(0.,stock-lo)-H*ru/eta_d;d=max(0.,hi-stock)-H*rd*eta_c;unit='mwh'
                        end=number(r['E_end_mwh']);fu=min(stock,end)-lo-H*(P+ru)/eta_d
                        fd=hi-max(stock,end)+H*eta_c*(P-rd if down_enabled else P+ru)
                    else:
                        pg,pp=number(r['P_gen_mw']),number(r['P_pump_mw']);n=number(r['n_gen'])
                        pmax,pmin=number(r['unit_gen_max_mw']),number(r['unit_gen_min_mw']);eg=number(r['e_gen_mwh_per_1e4m3'])
                        if eg<=0:raise ValueError('Nonpositive generation conversion')
                        equal(P,pg-pp,label+'/net_power')
                        margin(n*pmax-pg-ru,label+'/power_up');margin(pg-n*pmin-rd,label+'/power_down')
                        if not eligible:equal(ru+rd,0.,label+'/non_generating_zero')
                        lo=number(r['V_min_1e4m3']);hi=number(r['V_max_1e4m3']);stock=number(r['V_start_1e4m3'])
                        for endpoint in (('end',) if t==0 else ('start','end')):
                            v=number(r[f'V_{endpoint}_1e4m3']);margin(v-lo,label+'/stock_lower');margin(hi-v,label+'/stock_upper')
                        u=max(0.,stock-lo)-H*ru/eg;d=max(0.,hi-stock)-H*rd/eg;unit='1e4m3'
                        end=number(r['V_end_1e4m3']);fu=min(stock,end)-lo-H*(pg+ru)/eg
                        fd=hi-max(stock,end)+H*(pg-rd if down_enabled else pg+ru)/eg
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
    out['passed']=not out['violations']
    return out


if __name__=='__main__':
    result=audit(json.loads(Path(sys.argv[1]).read_text()))
    print(json.dumps(result,ensure_ascii=False,indent=2))
    raise SystemExit(0 if result['passed'] else 1)
