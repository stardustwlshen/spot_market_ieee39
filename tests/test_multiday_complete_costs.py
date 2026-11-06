"""Explicit complete spot-policy micro fixture; no legacy-cost fallback."""
import json
from copy import deepcopy
import yaml
from smm.series import run_series
from test_multiday_active_history import active_day
from multiday_replay import replay
from smm import spot_costs
from test_multiday_spot_financial import explicit_cost


def test_complete_costs_two_days_reconcile_without_zero_imputation(tmp_path):
    one,state=active_day('2026-07-15');two,_=active_day('2026-07-16',True)
    for number,day in enumerate((one,two),1):
        root=tmp_path/f'input_day_{number}';(root/'config').mkdir(parents=True)
        data=spot_costs.empty_profile();data['source']='Explicit synthetic spot acceptance; no observed plant costs and no legacy fallback'
        for family in ('thermal','hydro','storage','pumped'):
            for spec in day.cfg.units[family]:data['units'][spec['id']]=explicit_cost(spec.get('type',family),number)
        (root/'config/spot_market_costs.yaml').write_text(yaml.safe_dump(data,allow_unicode=True))
        day.cfg.root=root
    result=run_series([one,two],tmp_path/'series',initial_actual_state=state,log=lambda *a:None)
    money=result['financial'];assert money['all_costs_available'] is True
    assert money['cash_operating_cost_yuan'] is not None and money['simulated_operating_profit_yuan'] is not None
    assert abs(money['cash_revenue_yuan']-money['cash_operating_cost_yuan']-money['simulated_operating_profit_yuan'])<1e-7
    assert len(money['rows'])==2*24*6
    day_totals=[];hashes=[];replays=[]
    for date in ('2026-07-15','2026-07-16'):
        directory=tmp_path/'series'/('day_'+date)
        cash=json.loads((directory/'financial_full_precision.json').read_text())
        assert cash['summary']['all_unit_costs_available'] is True
        day_totals.append(cash['summary']);hashes.append(cash['summary']['cost_profile_sha256']);replays.append(replay(directory))
    assert hashes[0]!=hashes[1]
    for key in ('cash_revenue_yuan','cash_operating_cost_yuan','simulated_operating_profit_yuan'):
        assert abs(sum(day[key] for day in day_totals)-money[key])<1e-7
    report=dict(scope='complete_explicit_test_costs_spot_energy_cash_v2',
        all_unit_costs_available=True,subject_count=6,day_count=2,subject_period_count=len(money['rows']),
        totals={k:money[k] for k in ('cash_revenue_yuan','cash_operating_cost_yuan','simulated_operating_profit_yuan')},
        daily_cost_hashes=hashes,day_totals=day_totals,independent_replay=replays)
    (tmp_path/'complete_costs_report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print('COMPLETE_COST_EVIDENCE',tmp_path)
