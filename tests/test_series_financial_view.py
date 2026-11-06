from pathlib import Path
import sys
from streamlit.testing.v1 import AppTest
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'app'))
from series_financial_view import financial_display

def test_old_without_basis_stays_legacy():
    d=financial_display(dict(cash_revenue_yuan=-10.,cash_operating_cost_yuan=3.,simulated_operating_profit_yuan=-13.,all_costs_available=True))
    assert d['values']==(-10.,3.,-13.) and '历史口径' in d['labels'][2]

def test_mixed_costs_cannot_show_even_if_individually_available():
    d=financial_display(dict(profit_basis=None,cost_basis_consistent=False,aggregate_cost_profit_available=False,all_costs_available=True,
        cash_revenue_yuan=100.,cash_operating_cost_yuan=20.,simulated_operating_profit_yuan=80.,cost_aggregation_unavailable_reason='mixed_profit_basis_or_cost_version'))
    assert d['values']==(100.,None,None) and not d['available']

def test_spot_label_and_missing_costs_not_zero():
    d=financial_display(dict(profit_basis='spot_energy_cash_v2',aggregate_cost_profit_available=False,cash_revenue_yuan=-10.))
    assert d['labels'][2]=='现货利润（模拟）' and d['values']==(-10.,None,None)

def test_unknown_basis_not_relabelled():
    d=financial_display(dict(profit_basis='future_unknown',all_costs_available=True,cash_revenue_yuan=5.,cash_operating_cost_yuan=1.,simulated_operating_profit_yuan=4.))
    assert d['values']==(5.,None,None)

def test_spot_apptest_does_not_recompute():
    r=dict(profit_basis='spot_energy_cash_v2',aggregate_cost_profit_available=True,cost_basis_consistent=True,cash_revenue_yuan=-10.,cash_operating_cost_yuan=3.,simulated_operating_profit_yuan=-13.)
    at=AppTest.from_string(f"import sys\nsys.path.insert(0,{str(ROOT/'app')!r})\nfrom series_financial_view import render_series_financial\nrender_series_financial({r!r})").run()
    assert not at.exception and [x.value for x in at.metric]==['-10.00元','3.00元','-13.00元']
    assert any('不重复扣除' in x.value for x in at.caption)
