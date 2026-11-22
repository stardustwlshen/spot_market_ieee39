"""Verify the branch's actual default widget workflow with native HiGHS."""
import json
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from scripts.launch_sichuan import command

import sys

PROGRAM=Path(__file__).resolve().parents[1]


def test_new_default_no_legacy_fallback():
    assert Path(command(PROGRAM,'python',8501)[4]).name=='四川.py'
    assert Path(command(PROGRAM,'python',8501,'guangdong')[4]).name=='主页.py'
    with pytest.raises(ValueError,match='未知市场'):
        command(PROGRAM,'python',8501,'typo')


def test_native_entry_run_then_read_only_archive_and_tamper(tmp_path, monkeypatch):
    import sys
    monkeypatch.syspath_prepend(str(PROGRAM/'app'))
    import market_rule_profile
    monkeypatch.setattr(market_rule_profile.MarketProfile,'default_results',
        lambda self, _:tmp_path/self.results_folder)
    root=market_rule_profile.SICHUAN_V5.default_results(tmp_path)
    app=AppTest.from_file(str(PROGRAM/'app/四川.py'),default_timeout=100).run()
    assert not app.exception and not app.error
    assert '四川 V5.0' in app.title[0].value
    app.selectbox(key='scv5_month').select(5).run()
    assert any('报量不报价' in cap.value for cap in app.caption)
    app.slider(key='scv5_bid').set_value(360).run()
    app.button(key='scv5_run').click().run()
    assert not app.exception and not app.error
    paths=list(root.iterdir())
    assert len(paths)==1
    name=paths[0].name
    receipt=json.loads((paths[0]/'RECEIPT.json').read_bytes())
    assert receipt['status']=='partial_day_research' and receipt['quarters']==4
    assert receipt['formal_settlement'] is False
    inputs=json.loads((paths[0]/'INPUT.json').read_bytes())
    assert inputs['coal_offer']==360 and inputs['month']==5
    assert len(json.loads((paths[0]/'DA_PRECLEAR.json').read_bytes()))==96
    assert not (tmp_path/'runs').exists()
    fresh=AppTest.from_file(str(PROGRAM/'app/四川.py'),default_timeout=30).run()
    fresh.selectbox(key='scv5_archive').select(name).run()
    assert fresh.button(key='scv5_run').disabled
    assert not fresh.error
    fresh.selectbox(key='scv5_period_'+name).select(3).run()
    assert fresh.selectbox(key='scv5_period_'+name).value==3
    assert len(list(root.iterdir()))==1
    result=paths[0]/'RT_RESEARCH_VALUES.json'
    result.write_bytes(result.read_bytes()+b'\n')
    fresh.run()
    assert any('校验不匹配' in item.value for item in fresh.error)
    assert len(list(root.iterdir()))==1


def test_shed_period_does_not_create_research_money_or_clamp_raw_prices(monkeypatch):
    monkeypatch.syspath_prepend(str(PROGRAM/'app'))
    from sichuan_v5_demo import fixture, research_values
    case,_=fixture(7)
    raw={b:900. for b in case.buses}
    row={'quarter':0,'power':{r.id:0. for r in case.resources},'charge':{},
         'raw_nodal_prices':raw,'shed':{b:1. for b in case.buses},'spill':{b:0. for b in case.buses},
         'demand':{b:2. for b in case.buses},'reliability_reserve_short_mw':0.}
    record=research_values(case,row)
    assert record['uniform_price'] is None
    assert record['valuation_status']=='suppressed_reliability_slack'
    assert all(v is None for v in record['cleared_nodal_price'].values())
    assert all(r['net_amount'] is None for r in record['rows'] if r['role']=='user')
    assert set(raw.values())=={900.}


def test_archive_intraday_plan_only_after_effective_tick(tmp_path,monkeypatch):
    monkeypatch.syspath_prepend(str(PROGRAM/'app'))
    from market_rule_profile import SICHUAN_V5
    from sichuan_v5_intraday import Update
    from sichuan_v5_workflow import run
    storage=SICHUAN_V5.default_results(tmp_path)
    first_name,first=run(storage,month=7,periods=4,intraday_update=Update(4,1.02))
    assert first['receipt']['intraday_status']=='not_reached'
    assert first['intraday'] is None and not (storage/first_name/'ID_PLAN.json').exists()
    name,after=run(storage,month=7,periods=5,intraday_update=Update(4,1.02))
    assert after['receipt']['intraday_status']=='executed'
    assert after['rt'][:4]==first['rt']
    assert after['values'][:4]==first['values']
    assert after['intraday']['activation']['prior_executed_quarter']==3
    assert len(after['intraday']['rows'])==92
    assert not any('raw_nodal_prices' in row for row in after['intraday']['rows'])
    assert (storage/name/'ID_PLAN.json').exists()


def test_full_39_bus_combined_sequential_reserve_intraday_and_delayed_hydro(tmp_path,monkeypatch):
    monkeypatch.syspath_prepend(str(PROGRAM/'app'))
    from market_rule_profile import SICHUAN_V5
    from sichuan_v5_intraday import Update
    from sichuan_v5_workflow import run
    root=SICHUAN_V5.default_results(tmp_path)
    name,out=run(root,month=7,periods=96,hydro_cascade=True,reserve_gross=120.,
                 intraday_update=Update(48,1.02))
    assert out['receipt']['reserve_stage']=='research_cleared'
    assert out['receipt']['intraday_status']=='executed'
    assert out['reserve']['awards']['H1']['regular_mw']=='120.0'
    assert out['reserve']['ordinary_marginal_price_cny_per_mw_h']=='45'
    assert len(out['da'])==len(out['rt'])==len(out['values'])==96
    assert all(len(r['raw_nodal_prices'])==39 and r['ancillary_holds_mw']['H1'][0]==120.
               for r in out['rt'])
    assert all(v['valuation_status']=='provisional_rt_only' and v['uniform_price'] is not None
               for v in out['values'])
    assert not out['receipt']['formal_settlement']
    assert not out['receipt']['price_fallback_applied']
    assert len(out['intraday']['rows'])==48
    receipt=out['intraday']['activation']
    assert receipt['hydro_cascade_state']['period']==48
    assert receipt['prior_executed_quarter']==47
    assert receipt['hydro_cascade_state']['volume_m3']['H2']==pytest.approx(out['rt'][47]['hydro']['H2']['storage_m3'])
    assert out['rt'][48]['hydro']['H2']['upstream_arrival_m3']==pytest.approx(
        900*(out['rt'][46]['hydro']['H1']['turbine_m3s']+out['rt'][46]['hydro']['H1']['water_spill_m3s']))
    assert (root/name/'DA_COMMITMENT_RESEARCH.json').is_file()
