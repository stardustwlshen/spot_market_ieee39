"""Independent damage probes for retained financial archives and optional panels."""
from copy import deepcopy
from pathlib import Path
import sys

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'app'))
from settlement_validation import (cross_table_settlement_check, normalized_profit_table,
                                   validate_saved_settlement)
from final_views import settlement_identity


def result():
    detail = pd.DataFrame([dict(entity=u, type='coal', period=t, q_da=10., q_rt=12.,
                               lmp_da=20., lmp_rt=30., da_amount=50., rt_amount=15., total=65., dev_pnl=5.)
                           for u in ['G1', 'G2'] for t in [0, 1]])
    entity = detail.groupby(['entity', 'type'])[['da_amount', 'rt_amount', 'total', 'dev_pnl']].sum().reset_index()
    by_type = entity.groupby('type')[['da_amount', 'rt_amount', 'total', 'dev_pnl']].sum().reset_index()
    return dict(summary={'expected_periods': 96}, settlement_detail=detail, settlement_by_entity=entity,
                settlement_by_type=by_type, settlement_reconciliation=pd.DataFrame({'residual':[0.]}),
                rt_prices=pd.DataFrame({'period':[0, 1]}))


def test_intact_three_level_settlement_passes_without_mutation():
    r = result(); before = deepcopy(r)
    validate_saved_settlement(r)
    assert cross_table_settlement_check(r) == ([], [])
    assert settlement_identity(r)['通过（<1 元）'] is True
    for k, v in r.items():
        if isinstance(v, pd.DataFrame):pd.testing.assert_frame_equal(v, before[k])


@pytest.mark.parametrize('table', ['settlement_detail', 'settlement_by_entity', 'settlement_by_type'])
@pytest.mark.parametrize('damage', ['duplicate', 'string', 'nan', 'inf', 'amount', 'missing_group'])
def test_damaged_money_fails_before_rendering_and_cannot_pass_identity(table, damage):
    r=result(); frame=r[table]
    if damage=='duplicate':r[table]=pd.concat([frame, frame.iloc[[0]]],ignore_index=True)
    elif damage=='amount':frame.loc[0,'total']+=10_000_000
    elif damage=='missing_group':r[table]=frame.iloc[1:].copy()
    else:
        frame['total']=frame.total.astype(object)
        frame.loc[0,'total']={'string':'BAD','nan':float('nan'),'inf':float('inf')}[damage]
    with pytest.raises(ValueError):validate_saved_settlement(r)
    assert settlement_identity(r)['通过（<1 元）'] is not True


def test_missing_legacy_fields_remain_unknown_not_false_pass():
    r=result();r['settlement_by_type']=None
    assert cross_table_settlement_check(r)[1]
    assert settlement_identity(r)['通过（<1 元）'] is None


def test_csv_rounding_tolerance_and_numeric_string_normalization():
    r=result();r['settlement_by_entity']['total']=(r['settlement_by_entity'].total+.0001).astype(str)
    validate_saved_settlement(r)
    assert pd.api.types.is_numeric_dtype(r['settlement_by_entity'].total)
    assert settlement_identity(r)['通过（<1 元）'] is True


@pytest.mark.parametrize('payload', ['[]','null','{"artifacts":null}','{"artifacts":{}}'])
def test_optional_bad_manifest_is_local_warning(tmp_path,payload):
    (tmp_path/'output_manifest.json').write_text(payload)
    at=AppTest.from_string(f"from output_browser import render_output_browser\nrender_output_browser({str(tmp_path)!r})\nimport streamlit as st\nst.info('后续正常结果仍可显示')").run()
    assert not at.exception
    assert any('索引无法读取' in x.value for x in at.warning)
    assert any('后续正常结果' in x.value for x in at.info)
    assert (tmp_path/'output_manifest.json').read_text()==payload


@pytest.mark.parametrize('field', ['cash_revenue_yuan','simulated_operating_profit_yuan','cash_operating_cost_yuan'])
def test_bad_optional_profit_is_local_warning(field):
    rows=[dict(unit='G1',cost_available=True,cash_revenue_yuan=10.,cash_operating_cost_yuan=5.,simulated_operating_profit_yuan=5.)]
    rows[0][field]='BAD'
    at=AppTest.from_string(f"from market_views import operating_profit_dashboard\noperating_profit_dashboard({rows!r})\nimport streamlit as st\nst.info('物理结果仍可显示')").run()
    assert not at.exception
    assert any('成本与利润记录损坏' in x.value for x in at.warning)
    assert any('物理结果仍可显示' in x.value for x in at.info)


def test_missing_costs_do_not_become_zero_or_numeric_profit():
    rows=pd.DataFrame([dict(unit='G1',cost_available=False,cash_revenue_yuan=10.,simulated_operating_profit_yuan=123.)])
    safe=normalized_profit_table(rows)
    assert pd.isna(safe.simulated_operating_profit_yuan.iloc[0])
    assert rows.simulated_operating_profit_yuan.iloc[0]==123.


def test_consistently_corrupt_total_cannot_bypass_intrinsic_identity():
    r=result()
    r['settlement_detail']['total'] += 100.
    r['settlement_by_entity']['total'] += 200.
    r['settlement_by_type']['total'] += 400.
    with pytest.raises(ValueError,match='合计'):
        validate_saved_settlement(r)


def test_numeric_but_inconsistent_profit_is_not_presented():
    rows=pd.DataFrame([dict(unit='G1',cost_available=True,cash_revenue_yuan=10.,
                           cash_operating_cost_yuan=5.,simulated_operating_profit_yuan=999.)])
    with pytest.raises(ValueError,match='利润与'):
        normalized_profit_table(rows)
