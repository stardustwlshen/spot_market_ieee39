"""Read-only safety-reserve UI: no fabricated missing measurements or payments."""
from pathlib import Path
from streamlit.testing.v1 import AppTest

ROOT=Path(__file__).resolve().parents[1]


def render(tables):
    code=f"""
import sys
sys.path.insert(0,{str(ROOT/'app')!r})
from market_views import reserve_dashboard
reserve_dashboard({tables!r},prefix='test')
"""
    return AppTest.from_string(code,default_timeout=20).run()


def test_missing_old_snapshot_is_not_zero():
    at=render({'units':[{'period':0,'unit':'ES1','type':'storage','P_mw':10}],
               'system':[{'period':0,'reserve_up_short_max_win':37}]})
    assert not at.exception
    assert any('未记录' in info.value for info in at.info)
    assert not at.dataframe
    assert not at.get('plotly_chart')


def test_actual_first_period_and_unit_reserves_preserved():
    at=render({'units':[{'period':0,'unit':'ES1','type':'storage','P_mw':-10,'r_up':35.,'r_dn':7.}],
               'system':[{'period':0,'R_up_req':40.,'R_dn_req':10.,'R_up_supply':35.,'R_dn_supply':7.,
                          'R_up_short':5.,'R_dn_short':3.,'reserve_up_short_max_win':99.}]})
    assert not at.exception
    assert len(at.dataframe)==2
    assert at.dataframe[0].value['上备用短缺 MW'].iloc[0]==5
    assert 99 not in at.dataframe[0].value.to_numpy()
    assert at.dataframe[1].value.r_up.iloc[0]==35
    assert any('非独立交易市场' in x.value for x in at.subheader)
    assert any('不新增备用报价或付款' in x.value for x in at.caption)


def test_null_unit_values_remain_unrecorded_not_zero():
    at=render({'units':[{'period':0,'unit':'PS1','type':'pumped','P_mw':0,'r_up':None,'r_dn':None}]})
    assert not at.exception
    assert not at.dataframe
    assert any('该主体上/下备用未记录' in x.value for x in at.info)


def test_storage_capability_units_and_inactive_direction_null():
    at=render({'units':[{'period':0,'unit':'ES1','type':'storage','P_mw':-10,'r_up':35.,'r_dn':0.}],
               'storage':[{'period':0,'unit':'ES1','T_dur_h':1.,'reserve_power_up_margin_mw':20.,
                           'reserve_duration_up_margin_mwh':8.,'reserve_duration_down_margin_mwh':None}]})
    assert not at.exception
    assert at.dataframe[-1].value.reserve_duration_up_margin_mwh.iloc[0]==8
    assert at.dataframe[-1].value.reserve_duration_down_margin_mwh.isna().all()
    assert any('理想逆变器' in x.value for x in at.caption)
    assert any('万m³' in x.value and 'MWh' in x.value for x in at.caption)


def test_pumped_generation_only_semantics():
    at=render({'units':[{'period':0,'unit':'PS1','type':'pumped','P_mw':-200,'r_up':0.,'r_dn':0.}],
               'pumped':[{'period':0,'unit':'PS1','mode':'抽水','T_dur_h':1.,
                          'reserve_duration_up_margin_1e4m3':None,'reserve_duration_down_margin_1e4m3':None}]})
    assert not at.exception
    assert any('定速抽水及空闲不计备用' in x.value for x in at.caption)
    assert at.dataframe[-1].value.reserve_duration_up_margin_1e4m3.isna().all()


def test_qualification_is_separate_from_award_and_disables_duration_display():
    at=render({'units':[{'period':0,'unit':'ES1','type':'storage','P_mw':30,'r_up':0.,'r_dn':0.,
                        'reserve_eligible':True,'reserve_qualified':0},
                       {'period':1,'unit':'ES1','type':'storage','P_mw':30,'r_up':0.,'r_dn':0.,
                        'reserve_eligible':True,'reserve_qualified':1}],
               'storage':[{'period':0,'unit':'ES1','T_dur_h':1.,'reserve_duration_up_margin_mwh':-8.},
                          {'period':1,'unit':'ES1','T_dur_h':1.,'reserve_duration_up_margin_mwh':8.}]})
    assert not at.exception
    awards=at.dataframe[0].value
    assert awards.reserve_qualified.tolist()==[0,1]
    assert awards.r_up.tolist()==[0,0]
    capability=at.dataframe[-1].value
    assert capability.reserve_duration_up_margin_mwh.isna().tolist()==[True,False]
    assert capability.reserve_duration_up_margin_mwh.iloc[1]==8
    assert any('z=1也可能分配0MW' in x.value for x in at.caption)


def test_up_only_hides_down_awards_without_relabeling_physical_bounds():
    at=render({'units':[{'period':0,'unit':'ES1','type':'storage','P_mw':-10,'r_up':35.,'r_dn':0.}],
               'system':[{'period':0,'down_enabled':False,'R_up_req':40.,'R_dn_req':0.,
                          'R_up_supply':35.,'R_dn_supply':0.,'R_up_short':5.,'R_dn_short':0.}],
               'storage':[{'period':0,'unit':'ES1','T_dur_h':1.,'reserve_duration_up_margin_mwh':8.,
                           'reserve_duration_down_margin_mwh':None}]})
    assert not at.exception
    assert not any('下备用' in str(c) for c in at.dataframe[0].value.columns)
    assert 'r_dn' not in at.dataframe[1].value
    assert not any('_down_' in c for c in at.dataframe[2].value)
    assert any('物理SOC/库容上下界' in x.value for x in at.info)


def test_up_activation_retains_both_physical_bounds():
    at=render({'units':[{'period':0,'unit':'ES1','type':'storage','P_mw':-100,'r_up':10.,'r_dn':0.,'reserve_qualified':1}],
               'system':[{'period':0,'reserve_down_enabled':False}],
               'storage':[{'period':0,'unit':'ES1','T_dur_h':1.,'reserve_duration_lower_margin_mwh':80.,
                           'reserve_duration_upper_margin_mwh':2.,'reserve_duration_down_margin_mwh':2.,
                           'reserve_duration_up_margin_mwh':80.}]})
    assert not at.exception
    capability=at.dataframe[-1].value
    assert capability.reserve_duration_lower_margin_mwh.iloc[0]==80
    assert capability.reserve_duration_upper_margin_mwh.iloc[0]==2
    assert 'reserve_duration_down_margin_mwh' not in capability
    assert 'reserve_duration_up_margin_mwh' not in capability
    assert any('两侧都要核验' in x.value for x in at.caption)
