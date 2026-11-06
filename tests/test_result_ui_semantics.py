"""Chart units and soft-target/profit UI semantics; no optimization."""
from pathlib import Path
import sys
import json
import pytest
import plotly.graph_objects as go
from streamlit.testing.v1 import AppTest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'app'))
from common import style_fig


def test_secondary_axis_unit_survives_styling():
    for left,right in [('电量（MWh）','充放电功率（MW）'),('出力（MW）','库容（万m³）')]:
        fig=go.Figure(go.Scatter(x=[0],y=[1],yaxis='y2'))
        fig.update_layout(yaxis2=dict(title=right,overlaying='y',side='right'))
        spec=style_fig(fig,'test','时刻',left).to_plotly_json()
        assert spec['layout']['yaxis']['title']['text']==left
        assert spec['layout']['yaxis2']['title']['text']==right
        assert spec['data'][0]['yaxis']=='y2'


def render_hydro(kind):
    row=dict(unit='H1',window_start=0,window_end_exclusive=8,current_V_1e4m3=100.,current_P_mw=20.,
             target_V_1e4m3=100.,actual_terminal_V_1e4m3=101.,terminal_error_1e4m3=1.,ramp_violation_max_mw=0.)
    if kind is not None:row['terminal_kind']=kind
    history={'0':[row]}
    return AppTest.from_string(f"import sys\nsys.path.insert(0,{str(ROOT/'app')!r})\nfrom market_views import hydro_window_dashboard\nhydro_window_dashboard({history!r})",default_timeout=20).run()


def test_soft_target_deviation_not_falsely_hard_error():
    at=render_hydro('soft_da_window_end')
    assert not at.exception and not at.error
    assert any('非零目标偏差不等于物理约束违规' in x.value for x in at.info)


def test_hard_target_violation_remains_red():
    at=render_hydro('hard_da_window_end')
    assert not at.exception
    assert any('硬末目标' in x.value for x in at.error)


def test_unknown_history_does_not_infer_policy():
    at=render_hydro(None)
    assert not at.exception and not at.error
    assert any('未明确保存软/硬目标类型' in x.value for x in at.caption)


def render_profit(rows):
    return AppTest.from_string(f"import sys\nsys.path.insert(0,{str(ROOT/'app')!r})\nfrom market_views import operating_profit_dashboard\noperating_profit_dashboard({rows!r})",default_timeout=20).run()


@pytest.mark.parametrize('basis', [None, 'legacy_cash_operating_v1', 'spot_energy_cash_v2'])
def test_missing_profit_profile_not_zero_cost_or_fake_profit(basis):
    at=render_profit([dict(unit='H1',cash_revenue_yuan=100.,cost_available=False,
                          cost_missing_reason='missing',cash_operating_cost_yuan=0.,simulated_operating_profit_yuan=100.,
                          **({'profit_basis':basis} if basis else {}))])
    assert not at.exception
    df=at.dataframe[0].value
    profit_label = '现货利润（模拟） 元' if basis == 'spot_energy_cash_v2' else '历史口径利润 元'
    cost_label = '现货运行成本 元' if basis == 'spot_energy_cash_v2' else '历史口径运行成本 元'
    assert df[profit_label].isna().all()
    assert df[cost_label].isna().all()
    assert df['净能量现金结算 元'].iloc[0]==100.
    assert at.warning


@pytest.mark.parametrize('basis', [None, 'legacy_cash_operating_v1', 'spot_energy_cash_v2'])
def test_profit_keeps_signed_charging_item_not_double_deducted(basis):
    at=render_profit([dict(unit='ES1',cash_revenue_yuan=100.,charging_settlement_yuan=-20.,
                          cost_available=True,cash_operating_cost_yuan=30.,simulated_operating_profit_yuan=70.,
                          **({'profit_basis':basis} if basis else {}))])
    assert not at.exception
    df=next(x.value for x in at.dataframe if '其中充电/抽水结算 元（已含）' in x.value)
    profit_label = '现货利润（模拟） 元' if basis == 'spot_energy_cash_v2' else '历史口径利润 元'
    cost_label = '现货运行成本 元' if basis == 'spot_energy_cash_v2' else '历史口径运行成本 元'
    assert df[profit_label].iloc[0]==70.
    assert df[cost_label].iloc[0]==30.
    assert df['净能量现金结算 元'].iloc[0]==100.
    assert df['其中充电/抽水结算 元（已含）'].iloc[0]==-20.
    assert any('不再重复扣减' in x.value for x in at.caption)


def test_profit_missing_export_is_na():
    at=render_profit(None)
    assert not at.exception and not at.dataframe
    assert any('未记录' in x.value for x in at.info)


def test_completed_gap_and_scope_are_not_optimal_or_fullday_by_assumption(tmp_path):
    summary={'executed_periods':4,'expected_periods':96,'completed_scope':'debug_prefix',
             'checks':{'physical_feasible_checked_constraints':True,'rules_satisfied_no_slack':False}}
    (tmp_path/'stage_diagnostics.json').write_text(json.dumps({'da':{'scuc_preplan':{'summary':{'termination':'maxTimeLimit','gap':.0448}}}}))
    code=f"import sys\nsys.path.insert(0,{str(ROOT/'app')!r})\nfrom market_views import result_health_dashboard\nresult_health_dashboard({summary!r},{{'scuc_gap':.0448}},directory={str(tmp_path)!r})"
    at=AppTest.from_string(code,default_timeout=20).run()
    assert not at.exception
    assert any('达到计算时限' in x.value and '目标精度未记录' in x.value for x in at.warning)
    assert any('4 时段' in x.value for x in at.info)
    assert any('软目标偏差不等于物理违规' in x.value for x in at.info)


def test_empty_results_via_real_multipage_entrypoint(monkeypatch):
    import common
    monkeypatch.setattr(common,'all_results',lambda:[])
    at=AppTest.from_file(str(ROOT/'app/主页.py'),default_timeout=30).run()
    assert not at.exception
    at.switch_page('pages/3_结果.py').run()
    assert not at.exception
    assert any('还没有完成的运行结果' in x.value for x in at.info)
