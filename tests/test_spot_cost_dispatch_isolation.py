"""Real DA/ID/RT solves: changing accounting never changes the market."""
import json
from copy import deepcopy
import pandas as pd
import numpy as np
from test_causal_chain import stateful_case
from test_spot_costs import save,record
from smm.da import run_da
from smm.rolling import run_rolling,rt_tables
from smm.settlement import settle
from smm.profit import analyze


def test_real_twelve_period_cost_revision_changes_only_profit(tmp_path):
    records={'CHEAP':record('coal',fuel_cost_yuan_per_mwh=8,online_cost_yuan_per_h=2,startup_cost_yuan_per_start=10,shutdown_cost_yuan_per_stop=3),
        'LOCAL':record('gas_gt',fuel_cost_yuan_per_mwh=20,online_cost_yuan_per_h=4,startup_cost_yuan_per_start=50,shutdown_cost_yuan_per_stop=5),
        'ES1':record('storage'),'H1':record('hydro'),
        'PS1':record('pumped',gen_start_yuan_per_unit_start=3,pump_start_yuan_per_unit_start=5,gen_stop_yuan_per_unit_stop=2,pump_stop_yuan_per_unit_stop=1)}
    outputs=[]
    for i in range(2):
        root=tmp_path/f'cost_{i}';(root/'config').mkdir(parents=True)
        edited=deepcopy(records)
        if i:edited['CHEAP']['fuel_cost_yuan_per_mwh']=80
        save(root,edited);case=stateful_case();case.cfg.root=root
        da=run_da(case,verbose=False);rr=run_rolling(da,log=lambda *x:None);cash=settle(da,rr)
        profit=analyze(dict(da=da,rr=rr,settle=cash));outputs.append((da,rr,cash,profit))
    a,b=outputs
    assert len(a[1].rt)==len(b[1].rt)==12
    assert len(a[1].id_rounds)==len(b[1].id_rounds)==3
    assert a[0].case.bids==b[0].case.bids
    for key in ('P','u','Ph','Pd','Pc','Pg','Pp','E','Vh','Vu'):
        for x,y in zip(a[1].rt,b[1].rt):
            assert set(x['snap'][key])==set(y['snap'][key])
            for uid in x['snap'][key]:np.testing.assert_allclose(x['snap'][key][uid],y['snap'][key][uid],atol=1e-8,rtol=1e-8)
    pd.testing.assert_frame_equal(a[2]['detail'],b[2]['detail'],check_exact=False,atol=1e-8,rtol=1e-8)
    np.testing.assert_allclose(a[0].prices['lmp'],b[0].prices['lmp'],atol=1e-8,rtol=1e-8)
    cheap=a[3]['by_unit'].set_index('unit').loc['CHEAP']
    expected=72*cheap.energy_generated_or_discharged_mwh
    assert abs(b[3]['summary']['cash_operating_cost_yuan']-a[3]['summary']['cash_operating_cost_yuan']-expected)<1e-7
    assert a[3]['summary']['all_unit_costs_available'] and b[3]['summary']['all_unit_costs_available']
    (tmp_path/'isolation_report.json').write_text(json.dumps(dict(scope='synthetic_two_bus_12_periods_three_ID_rounds_not_full_IEEE39',dispatch_prices_and_settlement_equal=True,
        actual_cost_increase_yuan=expected,base=a[3]['summary'],revised=b[3]['summary']),indent=2))
    print('ISOLATION_EVIDENCE',tmp_path)
