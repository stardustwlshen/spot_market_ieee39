"""UI result scope and resource drilldown with a real HiGHS first hour."""
import json
from pathlib import Path

from streamlit.testing.v1 import AppTest

PROGRAM=Path(__file__).resolve().parents[1]


def test_real_widget_flow_first_hour_quantity_price_and_exclusions(tmp_path,monkeypatch):
    monkeypatch.syspath_prepend(str(PROGRAM/'app'))
    import market_rule_profile
    monkeypatch.setattr(market_rule_profile.MarketProfile,'default_results',
                        lambda self, _:tmp_path/self.results_folder)
    root=market_rule_profile.SICHUAN_V5.default_results(tmp_path)
    app=AppTest.from_file(str(PROGRAM/'app/四川.py'),default_timeout=100).run()
    app.button(key='scv5_run').click().run()
    assert not app.error and not app.exception
    name=next(root.iterdir()).name
    raw=(root/name/'RT_RESEARCH_VALUES.json').read_bytes()
    first=json.loads(raw)[0]['uniform_price']
    assert len(first.split('.')[1])>2
    assert app.metric[0].value==f'{float(first):,.2f}'
    assert any('本次覆盖 00:00–01:00（4/96' in c.value for c in app.caption)
    assert any('没有已记录的调频或备用容量占位' in c.value for c in app.caption)
    app.selectbox(key='scv5_period_'+name).select(3).run()
    assert any('当前时段：00:45–01:00' in m.value for m in app.markdown)
    app.selectbox(key='scv5_unit_'+name).select('C1').run()
    assert not app.exception
    compact=next(d.value for d in app.dataframe if '采用价格（元/MWh）' in d.value)
    assert compact.iloc[0]['实际计价口径']=='节点价'
    assert '日前参考量（MWh，不结算）' in compact
    app.selectbox(key='scv5_unit_'+name).select('G1').run()
    assert any('G1 不属于本算例现货金额测算范围' in i.value for i in app.info)
    assert (root/name/'RT_RESEARCH_VALUES.json').read_bytes()==raw
    assert len(list(root.iterdir()))==1


def test_submitted_bid_and_failure_keeps_previous_success(tmp_path,monkeypatch):
    monkeypatch.syspath_prepend(str(PROGRAM/'app'))
    import market_rule_profile
    import sichuan_v5_workflow as wf
    monkeypatch.setattr(market_rule_profile.MarketProfile,'default_results',
                        lambda self,_:tmp_path/self.results_folder)
    root=market_rule_profile.SICHUAN_V5.default_results(tmp_path)
    app=AppTest.from_file(str(PROGRAM/'app/四川.py'),default_timeout=90).run()
    app.number_input(key='scv5_bid').set_value(365)
    app.button(key='scv5_run').click().run()
    assert not app.exception and not app.error
    [good]=root.iterdir()
    assert json.loads((good/'INPUT.json').read_bytes())['coal_offer']==365
    assert any('365 元/MWh' in c.value for c in app.caption)
    get_error=lambda *a,**kw: (_ for _ in ()).throw(ValueError('合成故障：用于检查恢复体验'))
    monkeypatch.setattr(wf,'day_ahead',get_error)
    app.number_input(key='scv5_bid').set_value(380)
    app.button(key='scv5_run').click().run()
    assert app.button(key='scv5_run').disabled is False
    assert any('上一次成功结果' in e.value for e in app.error)
    assert any('365 元/MWh' in c.value for c in app.caption)
    failed,=set(root.iterdir())-{good}
    assert (failed/'FAILURE.json').is_file()
    assert json.loads((failed/'INPUT.json').read_bytes())['coal_offer']==380
    app.selectbox(key='scv5_archive').select(failed.name).run()
    assert not app.error and app.button(key='scv5_run').disabled
    assert any('合成故障' in item.value for item in app.warning)
    assert any('北京时间' in option for option in app.selectbox(key='scv5_archive').options)
    app.button(key='scv5_leave_archive').click().run()
    assert app.number_input(key='scv5_bid').value==380
    assert not app.button(key='scv5_run').disabled and len(list(root.iterdir()))==2
