"""Verify the branch's actual default widget workflow with native HiGHS."""
import json
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from scripts.launch_sichuan import command

import sys

PROGRAM=Path(__file__).resolve().parents[1]


def test_new_default_no_legacy_fallback():
    cmd=command(PROGRAM,'python',8501)
    assert Path(cmd[4]).name=='四川.py'
    assert cmd[cmd.index('--server.showEmailPrompt')+1]=='false'
    assert cmd[cmd.index('--client.showSidebarNavigation')+1]=='false'
    assert '--server.showEmailPrompt' not in command(PROGRAM,'python',8501,'guangdong')
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
    app.selectbox(key='scv5_month').select(5)
    assert any('报量不报价' in cap.value for cap in app.caption)
    app.number_input(key='scv5_bid').set_value(360)
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


def test_native_full_staged_agc_then_reserve_and_archive_evidence(tmp_path,monkeypatch):
    monkeypatch.syspath_prepend(str(PROGRAM/'app'))
    from decimal import Decimal as D
    from copy import deepcopy
    from market_rule_profile import SICHUAN_V5
    from sichuan_v5_intraday import Update
    from sichuan_v5_workflow import _check_segmented, load, run
    root=SICHUAN_V5.default_results(tmp_path)
    name,result=run(root,month=7,periods=96,hydro_cascade=True,reserve_gross=120.,
                    intraday_update=Update(48,1.02),clear_agc=True)
    assert result['receipt']['schema']=='ieee39.sichuan_v5.receipt.v3'
    assert result['receipt']['formal_settlement'] is False
    assert len(result['rt'])==96 and all(x['uniform_price'] is not None for x in result['values'])
    a,b=result['agc'],result['reserve']
    assert [(x['start'],x['stop']) for x in a]==[(0,48),(48,96)]
    assert [D(x['result']['system_regulation_credit_mw']) for x in a]==[D(40),D(60)]
    assert [D(x['result']['net_demand_mw']) for x in b]==[D(80),D(60)]
    for q,expected in [(47,[92.,12.]),(48,[78.,18.])]:
        assert result['rt'][q]['ancillary_holds_mw']['H1']==expected
        assert len(result['rt'][q]['raw_nodal_prices'])==39
    assert all(x['result']['regulation_stage_evidence_id']==y['result']['stage_evidence_id'] for x,y in zip(b,a))
    assert result['intraday']['activation']['hydro_cascade_state']['period']==48
    da=json.loads((root/name/'DA_POST_ANCILLARY_SCED_RESEARCH.json').read_bytes())
    wrong=deepcopy(b)
    wrong[0]['result']['energy_holds']['H1'][2]='80.01'
    with pytest.raises(ValueError,match='占位不一致'):
        _check_segmented(a,wrong,da,result['rt'],120.)
    assert load(root,name)['agc']==a
    path=root/name/'DA_AGC_RESEARCH.json'
    path.write_bytes(path.read_bytes()+b'\n')
    with pytest.raises(ValueError,match='校验不匹配'):
        load(root,name)


def test_segmented_agc_widget_selection_and_readonly_archive(tmp_path,monkeypatch):
    monkeypatch.syspath_prepend(str(PROGRAM/'app'))
    import market_rule_profile
    from decimal import Decimal as D
    monkeypatch.setattr(market_rule_profile.MarketProfile,'default_results',
        lambda self, _:tmp_path/self.results_folder)
    root=market_rule_profile.SICHUAN_V5.default_results(tmp_path)
    app=AppTest.from_file(str(PROGRAM/'app/四川.py'),default_timeout=120).run()
    app.checkbox(key='scv5_reserve_demo').check().run()
    app.checkbox(key='scv5_agc_demo').check().run()
    assert not any('已声明调频' in n.label for n in app.number_input)
    app.button(key='scv5_run').click().run()
    assert not app.error and not app.exception
    name=next(root.iterdir()).name
    assert any('抵扣备用' in c.value for c in app.caption)
    summary=next(d.value for d in app.dataframe if '抵扣后备用需求（MW）' in d.value)
    assert list(summary['时段'])==['00:00–12:00','12:00–24:00']
    assert list(map(D,summary['抵扣后备用需求（MW）']))==[D(80),D(60)]
    fresh=AppTest.from_file(str(PROGRAM/'app/四川.py'),default_timeout=30).run()
    fresh.selectbox(key='scv5_archive').select(name).run()
    assert not fresh.error and fresh.button(key='scv5_run').disabled
    assert len(list(root.iterdir()))==1


def test_segmented_real_shortage_never_emits_reserve_stage(monkeypatch):
    monkeypatch.syspath_prepend(str(PROGRAM/'app'))
    from sichuan_v5_agc_stages import stages
    from sichuan_v5_demo import fixture
    from decimal import Decimal as D
    case,_=fixture(7)
    # Three qualified units cannot reach 100% with the 30% per-unit cap.
    rows=[{'online':{'C1':0,'C2':1},'power':{r.id:r.capacity_mw*.5 for r in case.resources}}]*96
    reg=[]; reserve=[]
    with pytest.raises(ValueError,match='未运行备用或实时'):
        stages(case,rows,120.,publish_regulation=reg.extend,publish_reserve=reserve.extend)
    assert len(reg)==2 and not reserve
    assert [r['result']['unmet_mw'] for r in reg]==[D(4),D(6)]
