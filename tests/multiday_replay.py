"""Independent arithmetic replay of serialized actual first segments; no advance/optimizer."""
import json
from copy import deepcopy
from datetime import datetime,timedelta
from pathlib import Path


def replay(directory):
    root=Path(directory);e=json.loads((root/'actual_execution_full_precision.json').read_text())
    cp=json.loads((root/'actual_checkpoint.json').read_text());cfg=cp['source_cfg'];units=cfg['units'];dt=cfg['base']['dt_h']
    state=deepcopy(e['initial']['state']);initial=deepcopy(state);maxerr=0.
    def eq(a,b):
        nonlocal maxerr
        error=abs(float(a)-float(b));maxerr=max(maxerr,error);assert error<1e-7,(a,b)
    hydro={h['id']:h for h in units['hydro']};date=datetime.fromisoformat(e['initial']['state_at'])
    def pre(family,uid,key,index):
        record=initial[family+'_pre_history'][uid]
        start=datetime.fromisoformat(record['start']);offset=round(((date+timedelta(hours=index*dt)-start).total_seconds()/3600)/dt)
        values=record['values'] if key is None else record['values'][key]
        assert record['sources'][offset] in ('executed','research_assumption')
        return values[offset]
    for t,r in enumerate(e['records']):
        assert r['t']==t;v=r['values'];after=e['actual_states'][t+1]['state']
        for d in units['thermal']:
            g=d['id'];old=state['thermal'][g];on=bool(v['u'][g]);hours=old['hours']+dt if on==old['online'] else dt
            if on!=old['online']:assert old['hours']+1e-7 >= d['ut' if old['online'] else 'dt']
            eq(after['thermal'][g]['hours'],hours);eq(after['thermal'][g]['p0'],v['P'][g]);assert after['thermal'][g]['online']==on
        for d in units['storage']:
            s=d['id'];eq(after['storage'][s],state['storage'][s]+dt*(d['eta_c']*v['Pc'][s]-v['Pd'][s]/d['eta_d']));assert v['u_ch'][s]+v['u_dis'][s]<=1
        for d in units['hydro']:
            h=d['id'];season=d['seasons'][cfg['base']['season']];up=d.get('upstream');k=t-d.get('lag_periods',0)
            inflow=0. if not up else (e['records'][k]['values']['Qout'][up] if k>=0 else pre('hydro',up,None,k))
            eq(after['hydro'][h],state['hydro'][h]+.36*dt*(season['qin']+inflow-v['Qout'][h]))
            eq(after['hydro_used'][h],state['hydro_used'][h]+dt*v['Ph'][h]);eq(after['hydro_p'][h],v['Ph'][h]);eq(after['hydro_online'][h],r['hydro_online'][h])
            assert v['Qout'][h]+1e-7 >= d['water_rate']*v['Ph'][h]
        for d in units['pumped']:
            p=d['id'];eq(after['pumped'][p]['V'],state['pumped'][p]['V']+dt*(v['Pp'][p]/d['e_pump']-v['Pg'][p]/d['e_gen']));assert v['xg'][p]+v['xp'][p]<=1
            for age in range(1,d['switch_gap']+1):
                k=t-age
                for key,opposite in (('xg','xp'),('xp','xg')):
                    prev=e['records'][k]['values'][key][p] if k>=0 else pre('pumped',p,key,k)
                    assert prev+v[opposite][p]<=1
            for label,count,sn in (('gen','ng','ngen'),('pump','np','npump')):
                eq(after['pumped_used'][p][label],state['pumped_used'][p][label]+max(v[sn][p]-state['pumped'][p][count],0.));eq(after['pumped'][p][count],v[sn][p])
        for unit in r['hydro_units']:
            h,u=unit['station'],unit['unit_id'];old=state['hydro_commitment']['stations'][h]['units'][u];new=after['hydro_commitment']['stations'][h]['units'][u]
            now=date+timedelta(hours=t*dt);on=bool(unit['online'])
            if on!=old['online']:
                assert now>=datetime.fromisoformat(old['required_until'].replace('Z','+00:00'));eq(new['elapsed_h'],dt)
                assert datetime.fromisoformat(new['required_until'].replace('Z','+00:00'))==now+timedelta(hours=hydro[h]['ut' if on else 'dt'])
            else:eq(new['elapsed_h'],old['elapsed_h']+dt)
            assert new['online']==on
        state=deepcopy(after)
    assert state==cp['state']['state']
    profile=json.loads((root/'fixed_cost_profile.json').read_text());financial=json.loads((root/'financial_full_precision.json').read_text());costs=profile['units'];cash=e['settlement_detail']
    basis=financial['summary'].get('profit_basis','legacy_cash_operating_v1')
    if basis not in ('legacy_cash_operating_v1','spot_energy_cash_v2'):raise ValueError('Unknown archived accounting policy')
    if basis=='spot_energy_cash_v2':assert financial['summary']['cost_basis_version']=='spot-market-costs-v1'
    for row in financial['rows']:
        uid,t,kind=row['unit'],row['period'],row['type'];v=e['records'][t]['values'];before=e['actual_states'][t]['state']
        names=[uid] if kind not in ('storage','pumped') else ([uid+'_放电',uid+'_充电'] if kind=='storage' else [uid+'_发电',uid+'_抽水'])
        eq(row['cash_revenue_yuan'],sum(c['total'] for c in cash if c['entity'] in names and c['period']==t))
        if not row['cost_available']:
            assert row['cash_operating_cost_yuan'] is None and row['simulated_operating_profit_yuan'] is None;continue
        d=costs[uid];online=start=pumpstart=0.
        if kind in ('coal','gas_ccgt','gas_gt'):power=v['P'][uid];online=v['u'][uid];start=int(online and not before['thermal'][uid]['online'])
        elif kind=='hydro':power=v['Ph'][uid]
        elif kind in ('wind','pv'):power=v['Pw'][uid]
        elif kind=='storage':power=v['Pd'][uid]
        else:power=v['Pg'][uid];start=max(v['ngen'][uid]-before['pumped'][uid]['ng'],0);pumpstart=max(v['npump'][uid]-before['pumped'][uid]['np'],0)
        if basis=='spot_energy_cash_v2':
            stop=pumpstop=0.
            if kind in ('coal','gas_ccgt','gas_gt'):stop=int(not online and before['thermal'][uid]['online'])
            elif kind=='pumped':
                stop=max(before['pumped'][uid]['ng']-v['ngen'][uid],0.)
                pumpstop=max(before['pumped'][uid]['np']-v['npump'][uid],0.)
            cost=(power*dt*d.get('fuel_cost_yuan_per_mwh',0.)+online*dt*d.get('online_cost_yuan_per_h',0.)
                  +start*d.get('startup_cost_yuan_per_start',d.get('gen_start_yuan_per_unit_start',0.))
                  +pumpstart*d.get('pump_start_yuan_per_unit_start',0.)
                  +stop*d.get('shutdown_cost_yuan_per_stop',d.get('gen_stop_yuan_per_unit_stop',0.))
                  +pumpstop*d.get('pump_stop_yuan_per_unit_stop',0.))
            eq(row['shutdown_count'],stop);eq(row['pump_shutdown_count'],pumpstop)
        else:
            rate=d.get('scalar_marginal_cost_yuan_per_mwh',d.get('discharge_degradation_yuan_per_mwh',d.get('generation_vom_yuan_per_mwh')))
            cost=power*dt*rate+online*dt*d.get('fixed_online_cost_yuan_per_h',0)+start*d.get('fixed_startup_cost_yuan_per_start',d.get('gen_start_yuan_per_unit_start',0))+pumpstart*d.get('pump_start_yuan_per_unit_start',0)
        eq(row['cash_operating_cost_yuan'],cost);eq(row['simulated_operating_profit_yuan'],row['cash_revenue_yuan']-cost)
    return dict(executed_periods=len(e['records']),max_numeric_error=maxerr,thermal_duration=True,hydro_commitment=True,hydro_water=True,storage=True,pumped_switch_gap=True,cash_and_cost=True)
