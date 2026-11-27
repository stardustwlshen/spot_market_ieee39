"""Exercise the default page and the archived view through Streamlit controls."""
from pathlib import Path

from streamlit.testing.v1 import AppTest


PROGRAM = Path(__file__).resolve().parents[1]


def test_bidder_operator_and_settlement_views_share_frozen_result(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(PROGRAM/'app'))
    import market_rule_profile
    from sichuan_v5_dadu_run import list_dadu, read_dadu

    monkeypatch.setattr(market_rule_profile.MarketProfile, 'default_results', lambda self, _:tmp_path)
    app = AppTest.from_file(str(PROGRAM/'app/四川.py'), default_timeout=180).run()
    assert not app.exception
    assert '大渡河' in app.title[0].value
    assert app.button(key='scv5_run')  # Historical launcher still accessible in its own tab.
    assert list_dadu(tmp_path) == []
    app.selectbox(key='dadu_periods').select(4)
    app.button(key='dadu_freeze').click().run()
    app.button(key='dadu_run').click().run()
    assert not app.error and not app.exception
    [name] = list_dadu(tmp_path)
    result = read_dadu(tmp_path, name)
    app.radio(key='dadu_workspace').set_value('市场结果').run()
    assert app.selectbox(key='dadu_unit_'+name).value == 'D10'
    assert len(result['rt']) == 4
    assert any('4/96' in text.value for text in app.caption)
    assert len(app.get('vega_lite_chart')) >= 9
    app.selectbox(key='dadu_unit_'+name).select('D19')
    app.selectbox(key='dadu_q_'+name).select(3).run()
    assert not app.error and not app.exception
    assert any('安谷' in metric.label for metric in app.metric)
    from sichuan_v5_dadu_portfolio import portfolio_offers
    stations, offers = portfolio_offers(result, 3)
    assert len(stations) == 19 and offers[offers['电站']=='安谷'].iloc[-1]['终点 MW'] == result['inputs']['stations'][-1]['capacity_mw']
    assert any('公司冻结申报' in node.value for node in app.subheader)
    gas = next(table.value for table in app.dataframe if {'计划 MW','实发 MW'} <= set(table.value))
    assert len(gas) == 8 and (gas['计划 MW'] == gas['实发 MW']).all()
    grid = next(table.value for table in app.dataframe if '使用率 %' in table.value)
    assert grid['使用率 %'].max() <= 100.01
    market_asset = f'dadu_market_{name}_D19'
    app.selectbox(key=market_asset).select('COAL1').run()
    assert not app.error and not app.exception
    app.selectbox(key=market_asset).select('GAS1').run()
    assert any('气电执行保供计划，没有竞价申报' in text.value for text in app.caption)
    assert len(list_dadu(tmp_path)) == 1
    fresh = AppTest.from_file(str(PROGRAM/'app/四川.py'), default_timeout=60).run()
    fresh.radio(key='dadu_workspace').set_value('市场结果').run()
    fresh.selectbox(key='dadu_saved').select(name).run()
    assert not fresh.error and not fresh.exception
    assert any('组合现货毛收入' == m.label for m in fresh.metric)


def test_rt_reliability_separates_nodal_maxima_and_system_totals(monkeypatch):
    import pytest
    monkeypatch.syspath_prepend(str(PROGRAM/'app'))
    from sichuan_v5_dadu_view import reliability_rows
    rt = [{'quarter':11,'shed':{3:100.25,7:21.85},'spill':{3:217.32,5:409.18},'reliability_reserve_short_mw':4.0},
          {'quarter':12,'shed':{3:0.},'spill':{5:0.},'reliability_reserve_short_mw':0.}]
    money = {'quarters':[{'quarter':11,'reliable':False,'issues':['dispatch_not_verified']},
                         {'quarter':12,'reliable':True,'issues':[]}]}
    [diagnosis] = reliability_rows(rt,money)
    assert diagnosis['系统失负荷 MW']==pytest.approx(122.1)
    assert diagnosis['系统过剩 MW']==pytest.approx(626.5)
    assert diagnosis['该节点失负荷 MW']==100.25 and diagnosis['最大失负荷节点']=='3'
    assert diagnosis['该节点过剩 MW']==409.18 and diagnosis['最大过剩节点']=='5'
    assert diagnosis['备用缺额 MW']==4.
    assert '失负荷' in diagnosis['暂停计价或需复核原因']
