"""Complete explicit spot costs under the real compact daily execution path."""
import json,yaml
from smm import series,spot_costs,state_bridge as bridge,result_retention as retention
from test_multiday_active_history import active_day
from test_multiday_spot_financial import explicit_cost,replay_spot
from multiday_replay import replay


def test_archive_only_two_days_complete_spot_costs(tmp_path):
    one,initial=active_day('2026-07-15');two,_=active_day('2026-07-16',True)
    for n,day in enumerate((one,two)):
        day.cfg.mode['intraday'].update(enabled=True,interval_h=6.,mip_rel_gap=0.)
        day.cfg.base['solver']['mip_rel_gap']=0.
        day.cfg.mode['realtime']['mip_rel_gap']=0.
        source=tmp_path/f'inputs_{n}';(source/'config').mkdir(parents=True)
        profile=spot_costs.empty_profile();profile['source']='Explicit synthetic low-memory acceptance costs, not observed costs'
        for family in ('thermal','hydro','storage','pumped'):
            for unit in day.cfg.units[family]:profile['units'][unit['id']]=explicit_cost(unit.get('type',family),n)
        (source/'config/spot_market_costs.yaml').write_text(yaml.safe_dump(profile,allow_unicode=True))
        day.cfg.root=source;day.bid_receipt['confirmation_source']='explicit_automated_acceptance_not_user_manual_confirmation'
    root=tmp_path/'series'
    result=series.run_series([one,two],root,initial_actual_state=initial,result_retention='archive_only',log=lambda *a:None)
    assert result['new_results']==[] and len(result['new_result_refs'])==2
    cash=result['financial']
    assert cash['profit_basis']=='spot_energy_cash_v2' and cash['all_costs_available'] and cash['aggregate_cost_profit_available']
    assert len(cash['rows'])==288 and len({r['cost_profile_sha256'] for r in cash['cost_profiles_by_date']})==2
    assert abs(cash['cash_revenue_yuan']-cash['cash_operating_cost_yuan']-cash['simulated_operating_profit_yuan'])<1e-7
    reports=[];daily=[]
    for date in ('2026-07-15','2026-07-16'):
        d=root/f'day_{date}';reports.append(dict(spot=replay_spot(d),physical=replay(d)))
        s=json.loads((d/'summary.json').read_text());assert s['intraday']['rounds']==4
        assert retention.audit_archive(d,s)['valid']
        money=json.loads((d/'financial_full_precision.json').read_text());daily.append(money['summary'])
        assert all(r['cost_available'] for r in money['rows'])
        assert abs(money['summary']['cash_revenue_yuan']-money['summary']['cash_operating_cost_yuan']-money['summary']['simulated_operating_profit_yuan'])<1e-7
    for name in ('cash_revenue_yuan','cash_operating_cost_yuan','simulated_operating_profit_yuan'):
        assert abs(cash[name]-sum(x[name] for x in daily))<1e-7
    (tmp_path/'compact_complete_spot_report.json').write_text(json.dumps(bridge.plain(dict(scope='real synthetic 2bus 24x1h two days, 4ID per day, archive_only, complete explicit spot costs; not IEEE39',financial={k:v for k,v in cash.items() if k!='rows'},daily=daily,replay=reports)),ensure_ascii=False,indent=2))
