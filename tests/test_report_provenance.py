"""Cheap report provenance and scope regressions; no optimization."""
from pathlib import Path
import sys
import numpy as np
import pandas as pd
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import common


def test_legacy_persisted_ratings_win_over_current_configuration(tmp_path):
    da = pd.DataFrame({'line':['L1'], 'period':[0], 'flow_mw':[90.], 'rate_mw':[100.],
                       'mu_plus':[0.], 'mu_minus':[0.]})
    r={'snapshot_root':str(tmp_path/'missing'), 'da_line_flows':da,
       'rt_lines':da.drop(columns='rate_mw')}
    for stage in ('da','rt'):
        result=common.line_loading(r,stage)
        assert result.rate_eff.tolist()==[100.]
        assert result.loading.tolist()==[.9]


def test_legacy_missing_ratings_remain_unknown(tmp_path):
    df=pd.DataFrame({'line':['L1'], 'period':[0], 'flow_mw':[90.],
                     'mu_plus':[0.], 'mu_minus':[0.]})
    r={'snapshot_root':str(tmp_path/'missing'), 'da_line_flows':df, 'rt_lines':df}
    assert common.line_loading(r,'rt').loading.isna().all()
    assert common.branches_with_rate(str(tmp_path/'missing')).rate_eff.isna().all()


def test_load_result_never_borrows_current_unit_configuration(tmp_path):
    r=common.load_result(str(tmp_path),0.)
    assert r['snapshot_root']==str(tmp_path/'scenario_snapshot')
    assert not r['input_provenance_known']
    assert common.bus_resources(r['snapshot_root'])=={}


def test_report_price_objective_and_scope_labels():
    root=Path(__file__).resolve().parents[1]
    text=(root/'app/pages/3_结果.py').read_text()
    assert '原始节点电价 = 能量分量 + 阻塞分量' in text
    assert '"lmp_raw": "原始节点电价 元/MWh"' in text
    assert '日前总报价成本' not in text
    assert '元/可交付放电 MWh' in text
    compare=(root/'app/pages/4_场景对比.py').read_text()
    assert 'if same_rt_periods:' in compare
    assert '不计算结算总额差' in compare
