"""Business result hierarchy: diagnostics retained but closed, never replacing quantities."""
from pathlib import Path
import sys
import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'app'))

def test_actual_results_business_first_and_technical_collapsed():
    import common
    catalog=common.all_results()
    if not catalog:pytest.skip('real saved run unavailable')
    full=next((m for m in catalog if Path(m['dir']).joinpath('rt_prices.csv').exists()),None)
    if full is None:pytest.skip('full saved result unavailable')
    prices=pd.read_csv(Path(full['dir'])/'da_nodal_prices.csv')
    before=prices.copy(deep=True)
    at=AppTest.from_file(str(ROOT/'app/主页.py'),default_timeout=60).run()
    at.session_state['result_sel']=full['dir']
    at.switch_page('pages/3_结果.py').run()
    assert not at.exception
    assert all(label in [t.label for t in at.tabs] for label in ['运行总览','结算电价与电量','出力计划与实际','收入、成本与利润'])
    assert '诊断' not in [t.label for t in at.tabs]
    advanced=next(e for e in at.expander if e.label=='高级诊断与技术记录（按需查看）')
    assert advanced.proto.expanded is False
    assert any('混合整数相对间隙' in c.value for c in advanced.caption)
    assert any('辅助模型数量不执行' in c.value for c in advanced.caption)
    table=next(d.value for d in at.dataframe if set(d.value.columns)=={'时段序号','时刻','节点','限价后结算电价 元/MWh'})
    pd.testing.assert_series_equal(table['限价后结算电价 元/MWh'].reset_index(drop=True),before.lmp,check_names=False)
    pd.testing.assert_frame_equal(pd.read_csv(Path(full['dir'])/'da_nodal_prices.csv'),before)
    assert any('逐主体出力与中标电量' in s.value for s in at.subheader)
    assert not any('间隙' in m.label for m in at.metric)

    profit_table=next(d.value for d in at.dataframe if '模拟运营利润 元' in d.value and '净能量现金结算 元' in d.value)
    saved_profit=pd.read_csv(Path(full['dir'])/'operating_profit_by_unit.csv')
    pd.testing.assert_series_equal(profit_table['模拟运营利润 元'].reset_index(drop=True),saved_profit.simulated_operating_profit_yuan,check_names=False)
    assert (profit_table['模拟运营利润 元']<0).sum()==(saved_profit.simulated_operating_profit_yuan<0).sum()
    assert [t.label for t in advanced.tabs][:3]==['线路','网络拓扑','主体与求解阶段']
