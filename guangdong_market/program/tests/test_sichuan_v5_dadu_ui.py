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
    app.button(key='dadu_run').click().run()
    assert not app.error and not app.exception
    [name] = list_dadu(tmp_path)
    result = read_dadu(tmp_path, name)
    assert app.selectbox(key='dadu_unit_'+name).value == 'D10'
    assert len(result['rt']) == 4
    assert any('4/96' in text.value for text in app.caption)
    assert len(app.get('vega_lite_chart')) >= 9
    app.selectbox(key='dadu_unit_'+name).select('D19')
    app.selectbox(key='dadu_q_'+name).select(3).run()
    assert not app.error and not app.exception
    assert any('安谷' in metric.label for metric in app.metric)
    bids = next(table.value for table in app.dataframe if '申报价（元/MWh）' in table.value)
    assert bids.iloc[-1]['终点 MW'] == result['inputs']['stations'][-1]['capacity_mw']
    gas = next(table.value for table in app.dataframe if {'计划 MW','实发 MW'} <= set(table.value))
    assert len(gas) == 8 and (gas['计划 MW'] == gas['实发 MW']).all()
    grid = next(table.value for table in app.dataframe if '使用率 %' in table.value)
    assert grid['使用率 %'].max() <= 100.01
    market_asset = f'dadu_market_{name}_D19'
    app.selectbox(key=market_asset).select('COAL1').run()
    assert not app.error and not app.exception
    app.selectbox(key=market_asset).select('GAS1').run()
    assert any('气电执行保供计划，没有竞价申报' in text.value for text in app.caption)
    app.selectbox(key='dadu_strategy').select('naive').run()
    assert len(list_dadu(tmp_path)) == 1
    assert any('水价值报价' in text.value and name in text.value for text in app.caption)
    fresh = AppTest.from_file(str(PROGRAM/'app/四川.py'), default_timeout=60).run()
    fresh.selectbox(key='dadu_saved').select(name).run()
    assert not fresh.error and not fresh.exception
    assert any('组合现货毛收入' == m.label for m in fresh.metric)


def test_six_case_comparison_passes_common_controls_and_checks_boundary(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(PROGRAM/'app'))
    import market_rule_profile
    import sichuan_v5_dadu_run

    monkeypatch.setattr(market_rule_profile.MarketProfile, 'default_results', lambda self, _:tmp_path)
    calls = []
    def simulated_run(root, **kw):
        calls.append(kw)
        return 'test', {'rt':[{}]*kw['periods'], 'inputs':dict(kw,source_hashes={'model':'test'}),
                        'company':{'strategy':kw['strategy'],'company_forecast_sha256':'forecast'}, 'money':{'portfolio':[{'energy_mwh':5}]},
                        'summary':{'max_slack_mw':0, 'company_revenue':300, 'water_spill_m3':0,
                        'solar_curtail_mwh':3, 'gas_mwh':20, 'boundary_hash':kw['scenario'],
                        'issued_boundary_hash':'issue','bid_hash':kw['strategy']}}
    monkeypatch.setattr(sichuan_v5_dadu_run, 'run_dadu', simulated_run)
    app = AppTest.from_file(str(PROGRAM/'app/四川.py'), default_timeout=30).run()
    app.slider(key='dadu_gas_scale').set_value(.5)
    app.selectbox(key='dadu_periods').select(4)
    app.button(key='dadu_compare').click().run()
    assert not app.exception and not app.error
    assert len(calls) == 6 and len({(k['scenario'], k['strategy']) for k in calls}) == 6
    assert all(k['periods']==4 and k['gas_plan_scale']==.5 for k in calls)
    table = next(table.value for table in app.dataframe if '报价策略' in table.value)
    assert len(table) == 6
    app.number_input(key='dadu_seed').set_value(40).run()
    assert not any('报价策略' in frame.value for frame in app.dataframe)
    assert any('当前参数已改变' in notice.value for notice in app.info)


def test_matching_comparison_archives_are_read_without_solver(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(PROGRAM/'app'))
    import sichuan_v5_dadu_run as backend
    import sichuan_v5_dadu_view as view

    controls = dict(water_value_scale=1., corridor_scale=1., gas_plan_scale=1.)
    receipts = {s+'_'+b:dict(scenario=s, strategy=b, seed=39, periods=4, **controls)
                for s in view.SCENARIOS for b in view.STRATEGIES}
    calls = []
    def load(root, name):
        calls.append(name)
        return {'rt':[{}]*4, 'inputs':dict(receipts[name], source_hashes={'current':'abc'}),
                 'company':{'strategy':receipts[name]['strategy'],'company_forecast_sha256':'forecast'},
                'money':{'portfolio':[{'energy_mwh':5}]},
                'summary':{'issued_boundary_hash':'issue','bid_hash':receipts[name]['strategy'],'max_slack_mw':0., 'company_revenue':100., 'water_spill_m3':0., 'solar_curtail_mwh':0.,
                           'gas_mwh':20., 'boundary_hash':receipts[name]['scenario']}}
    monkeypatch.setattr(backend, 'read_dadu', load)
    monkeypatch.setattr(backend, 'source_hashes', lambda:{'current':'abc'})
    def unexpected(*args, **kwargs):
        raise AssertionError('identical archives must not launch a solver')
    monkeypatch.setattr(backend, 'run_dadu', unexpected)
    app = AppTest.from_string(
        "from sichuan_v5_dadu_view import _comparison\n"
        f"_comparison({str(tmp_path)!r}, 39, 4, {controls!r}, {receipts!r})"
    ).run()
    app.button(key='dadu_compare').click().run()
    assert not app.error and not app.exception
    assert set(calls) == set(receipts)
    original = load
    def mismatched(root, name):
        result = original(root, name)
        if receipts[name]['strategy']=='naive':
            result['summary']['issued_boundary_hash'] = 'different'
        return result
    monkeypatch.setattr(backend,'read_dadu',mismatched)
    app.button(key='dadu_compare').click().run()
    assert any('可比档案' in message.value for message in app.error)
    assert not app.dataframe


def test_stale_source_rejected_failure_has_no_partial_or_previous_comparison(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(PROGRAM/'app'))
    import sichuan_v5_dadu_run as backend
    import sichuan_v5_dadu_view as view

    settings = dict(water_value_scale=1., corridor_scale=1., gas_plan_scale=1.)
    controls = dict(scenario='wet_low', strategy='water_value', seed=39, periods=4, **settings)
    # A failed run has no completed receipt and must never enter the archive picker.
    broken = tmp_path/'dadu19'/'dadu19_20261127T000000Z_111111111111'
    broken.mkdir(parents=True)
    (broken/'failure.json').write_text('{}')
    assert backend.list_dadu(tmp_path)==[]
    monkeypatch.setattr(backend, 'source_hashes', lambda:{'current':'new'})
    monkeypatch.setattr(backend, 'read_dadu', lambda root,name:{'inputs':dict(controls, source_hashes={'current':'old'})})
    calls=[]
    def failed_run(root, **kw):
        calls.append(kw)
        raise ValueError('测试缺少实际出清结果')
    monkeypatch.setattr(backend,'run_dadu',failed_run)
    app = AppTest.from_string(
        "from sichuan_v5_dadu_view import _comparison\n"
        f"_comparison({str(tmp_path)!r}, 39, 4, {settings!r}, {{'old':{controls!r}}})"
    )
    app.session_state['dadu_pair_results'] = (39,4,settings,[{'上一批':'成功'}])
    app.run().button(key='dadu_compare').click().run()
    assert calls==[controls] and any('测试缺少实际出清结果' in e.value for e in app.error)
    assert not app.exception and not app.dataframe
    money = {'portfolio':[{'energy_mwh':12}]}
    summary = dict(company_revenue=60, max_slack_mw=0., water_stock_value_change=10, water_adjusted_value=70,
                   water_spill_m3=0,solar_curtail_mwh=0,gas_mwh=0,boundary_hash='x')
    for n, slack, expected in ((4,0,None), (96,1,None), (96,0,70)):
        row = view.comparison_row('wet_low','water_value',
                                  {'summary':dict(summary,max_slack_mw=slack),'rt':[{}]*n,'money':money})
        assert row['全天含水库存估值的研究收益（元）']==expected
        assert row['库存及在途水估值变化（元）']==(10 if expected else None)


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
