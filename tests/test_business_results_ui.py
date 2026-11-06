"""Business result hierarchy: diagnostics retained but closed, never replacing quantities."""
from pathlib import Path
import sys
import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'app'))

def test_actual_results_business_first_and_technical_collapsed(monkeypatch):
    import common
    import os
    supplied=os.environ.get("IEEE39_SAVED_RUN_FOR_UI")
    if supplied:
        monkeypatch.setattr(common,"all_results",lambda:[dict(dir=supplied,scenario="验收只读存档",mode="full",status="done")])
    catalog=common.all_results()
    if not catalog:pytest.skip('real saved run unavailable')
    import json
    def _aux(m):  # 只选记录了辅助定价来源的完整存档（早期存档缺该字段，不能据此断言说明文字）
        try:return json.loads(Path(m['dir']).joinpath('summary.json').read_text()).get('da_summary',{}).get('pricing_source')=='bounded_auxiliary_pricing'
        except Exception:return False
    # 本用例选择无日内存档；三块结构也由独立的启用日内用例覆盖
    def _intraday(m):  # 同 3_结果.py 的 INTRADAY 判定
        try:return bool((json.loads(Path(m['dir']).joinpath('summary.json').read_text()).get('intraday') or {}).get('enabled'))
        except Exception:return False
    full=next((m for m in catalog if Path(m['dir']).joinpath('rt_prices.csv').exists() and _aux(m) and not _intraday(m)),None)
    if full is None:pytest.skip('full saved result unavailable')
    prices=pd.read_csv(Path(full['dir'])/'da_nodal_prices.csv')
    before=prices.copy(deep=True)
    at=AppTest.from_file(str(ROOT/'app/主页.py'),default_timeout=60).run()
    at.session_state['result_sel']=full['dir']
    at.switch_page('pages/3_结果.py').run()
    assert not at.exception
    # 第二轮J项：按阶段分块，每阶段同一组子页；最终结果以日前/实时直接对比为核心
    labels=[t.label for t in at.tabs]
    assert all(label in labels for label in ['日前','日内·实时滚动','最终结果','关键指标','节点电价','窗口浏览','电价对比','两部制结算恒等式','收入与成本'])
    assert labels.count('弃风弃光')==2 and labels.count('SCUC/SCED与定价结果')==2
    assert labels.index('诊断')>labels.index('最终结果')  # 诊断放在“最终结果”最后一个子页
    advanced=next(e for e in at.expander if e.label=='高级诊断与技术记录（按需查看）')
    assert advanced.proto.expanded is False
    assert any('混合整数相对间隙' in c.value for c in advanced.caption)
    assert any('辅助模型数量不执行' in c.value for c in advanced.caption)
    table=next(d.value for d in at.dataframe if set(d.value.columns)=={'时段序号','时刻','节点','限价后结算电价 元/MWh'})
    pd.testing.assert_series_equal(table['限价后结算电价 元/MWh'].reset_index(drop=True),before.lmp,check_names=False)
    pd.testing.assert_frame_equal(pd.read_csv(Path(full['dir'])/'da_nodal_prices.csv'),before)
    assert any('逐主体出力与中标电量' in s.value for s in at.subheader)
    assert not any('间隙' in m.label for m in at.metric)

    # 限定全主体收入成本页，不误选逐主体总览，并按档案口径核对。
    income_tab=next(t for t in at.tabs if t.label=='收入与成本')
    saved_profit=pd.read_csv(Path(full['dir'])/'operating_profit_by_unit.csv')
    spot='profit_basis' in saved_profit and saved_profit.profit_basis.eq('spot_energy_cash_v2').all()
    profit_column='现货利润（模拟） 元' if spot else '历史口径利润 元'
    profit_table=next(d.value for d in income_tab.dataframe if profit_column in d.value and '净能量现金结算 元' in d.value)
    pd.testing.assert_series_equal(profit_table[profit_column].reset_index(drop=True),saved_profit.simulated_operating_profit_yuan,check_names=False)
    assert (profit_table[profit_column]<0).sum()==(saved_profit.simulated_operating_profit_yuan<0).sum()
    # J项：线路负载/拓扑并入各阶段“电网与阻塞”子页（以小标题区分），不再单设页签
    assert '电网与阻塞' in [t.label for t in at.tabs]
    assert '主体与求解阶段' in [t.label for t in at.tabs]
    assert '电能量结算' in [t.label for t in at.tabs] and '成本与利润' in [t.label for t in at.tabs]
    assert {'日前系统接线图','实时系统接线图'} <= {s.value for s in at.subheader}
