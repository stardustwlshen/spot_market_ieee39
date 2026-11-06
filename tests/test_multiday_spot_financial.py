"""Synthetic full 24h x 2-day spot-policy accounting acceptance, not IEEE39."""
from copy import deepcopy
import hashlib,json
from pathlib import Path
import yaml
from smm import series,state_bridge as bridge,spot_costs
from test_multiday_active_history import active_day


def explicit_cost(typ,day):
    row=dict(type=typ,source='Explicit automated acceptance assumptions, not observed plant costs',source_kind='user_defined')
    row.update({key:0. for key in spot_costs.FIELDS[typ]})
    if typ in spot_costs.THERMAL:
        row.update(online_cost_basis='no_load_fuel',fuel_cost_yuan_per_mwh=11.+day*2,
                   online_cost_yuan_per_h=2.,startup_cost_yuan_per_start=7.,shutdown_cost_yuan_per_stop=3.)
    elif typ=='pumped':row.update(gen_start_yuan_per_unit_start=5.,pump_start_yuan_per_unit_start=6.,gen_stop_yuan_per_unit_stop=2.,pump_stop_yuan_per_unit_stop=3.)
    else:row['cost_scope']='no_fuel_or_defined_start_stop_costs_excluding_om'
    return row


def replay_spot(directory):
    root=Path(directory);e=json.loads((root/'actual_execution_full_precision.json').read_text());money=json.loads((root/'financial_full_precision.json').read_text())
    profile=yaml.safe_load((root/'scenario_snapshot/config/spot_market_costs.yaml').read_text());dt=e['initial']['dt_h'] if 'dt_h' in e['initial'] else 1.
    cp=json.loads((root/'actual_checkpoint.json').read_text());dt=cp['source_cfg']['base']['dt_h'];errors=[];start_total=stop_total=0.
    for row in money['rows']:
        uid,t,typ=row['unit'],row['period'],row['type'];r=profile['units'][uid];v=e['records'][t]['values'];before=e['actual_states'][t]['state']
        power=on=starts=pstarts=stops=pstops=0.
        if typ in ('coal','gas_ccgt','gas_gt'):
            power=v['P'][uid];on=v['u'][uid];starts=int(on and not before['thermal'][uid]['online']);stops=int(not on and before['thermal'][uid]['online'])
        elif typ=='pumped':
            starts=max(v['ngen'][uid]-before['pumped'][uid]['ng'],0);pstarts=max(v['npump'][uid]-before['pumped'][uid]['np'],0)
            stops=max(before['pumped'][uid]['ng']-v['ngen'][uid],0);pstops=max(before['pumped'][uid]['np']-v['npump'][uid],0)
        variable=power*dt*r.get('fuel_cost_yuan_per_mwh',0)
        online=on*dt*r.get('online_cost_yuan_per_h',0)
        start=starts*r.get('startup_cost_yuan_per_start',r.get('gen_start_yuan_per_unit_start',0))+pstarts*r.get('pump_start_yuan_per_unit_start',0)
        stop=stops*r.get('shutdown_cost_yuan_per_stop',r.get('gen_stop_yuan_per_unit_stop',0))+pstops*r.get('pump_stop_yuan_per_unit_stop',0)
        expected=variable+online+start+stop
        errors += [abs(row[k]-value) for k,value in [('variable_cost_yuan',variable),('fixed_online_cost_yuan',online),('startup_cost_yuan',start),('shutdown_cost_yuan',stop),('cash_operating_cost_yuan',expected),('simulated_operating_profit_yuan',row['cash_revenue_yuan']-expected)]]
        assert row['startup_count']==starts and row['pump_startup_count']==pstarts
        assert row['shutdown_count']==stops and row['pump_shutdown_count']==pstops
        start_total+=start;stop_total+=stop
    assert max(errors)<1e-7
    assert money['summary']['cost_profile_sha256']==hashlib.sha256((root/'scenario_snapshot/config/spot_market_costs.yaml').read_bytes()).hexdigest()
    return dict(max_error=max(errors),row_count=len(money['rows']),startup_yuan=start_total,shutdown_yuan=stop_total,
                profit_basis=money['summary']['profit_basis'],source='independent_arithmetic_from_actual_before_after_states_and_frozen_cost_yaml')


def test_real_spot_two_days(tmp_path):
    one,initial=active_day('2026-07-15');two,_=active_day('2026-07-16',True)
    for n,day in enumerate((one,two)):
        source=tmp_path/f'input_{n}';(source/'config').mkdir(parents=True)
        profile=spot_costs.empty_profile();profile['source']='Explicit automated two-day acceptance accounting only'
        for family in ('thermal','hydro','storage','pumped'):
            for u in day.cfg.units[family]:
                typ=u.get('type',family);profile['units'][u['id']]=explicit_cost(typ,n)
        (source/'config/spot_market_costs.yaml').write_text(yaml.safe_dump(profile,allow_unicode=True))
        day.cfg.root=source;day.bid_receipt['confirmation_source']='automated_acceptance_not_user_manual_confirmation'
    result=series.run_series([one,two],tmp_path/'series',initial_actual_state=initial,log=lambda *a:None)
    cash=result['financial'];assert cash['profit_basis']=='spot_energy_cash_v2' and cash['cost_basis_version']=='spot-market-costs-v1'
    assert cash['all_costs_available'] and cash['aggregate_cost_profit_available'] and cash['cost_basis_consistent']
    assert len(cash['rows'])==288 and len({x['cost_profile_sha256'] for x in cash['cost_profiles_by_date']})==2
    assert abs(cash['cash_revenue_yuan']-cash['cash_operating_cost_yuan']-cash['simulated_operating_profit_yuan'])<1e-7
    reports=[replay_spot(tmp_path/'series'/f'day_{date}') for date in ('2026-07-15','2026-07-16')]
    assert sum(x['startup_yuan'] for x in reports)>0 and sum(x['shutdown_yuan'] for x in reports)>0
    a,b=result['new_results'];assert a['rr'].states[-1].thermal==b['rr'].states[0].thermal
    assert a['rr'].states[-1].pumped==b['rr'].states[0].pumped
    # t0 positive-count starts/stops are evaluated from actual preceding day, not cfg idle.
    assert b['rr'].states[0].pumped['PS1']['ng']>0
    out=dict(scope='synthetic_2bus_24x1h_two_days_new_spot_policy_no_OM',financial={k:v for k,v in cash.items() if k!='rows'},replay=reports)
    (tmp_path/'spot_two_day_report.json').write_text(json.dumps(bridge.plain(out),ensure_ascii=False,indent=2))
