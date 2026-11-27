from pathlib import Path

import pytest


@pytest.fixture
def views(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]/'app'))
    import sichuan_v5_dadu_company_results
    return sichuan_v5_dadu_company_results


def test_quantity_price_bridge_uses_execution_and_preserves_missing(views):
    station = {'id':'A','bus':3}
    company = {'internal_plan_mw':[{'A':10}]*2, 'expected_price_yuan_mwh':[{'A':100}]*2,
               'marginal_water_cost_yuan_mwh':[{'A':85}]*2,
               'offers':{'A':{'segments':[[12,85],[8,105]]}}}
    result = {'company':company,'effective_offers':{'A':[[12,80],[8,100]]},
              'da':[{'quarter':i,'power':{'A':12}} for i in range(2)],
              'rt':[{'quarter':0,'power':{'A':18},'metered_mw':{'A':16}},
                    {'quarter':1,'power':{'A':2},'metered_mw':{'A':0}}],
              'money':{'quarters':[{'quarter':0,'effective_price_by_bus':{'3':150},'reliable':True},
                                   {'quarter':1,'effective_price_by_bus':{3:None},'reliable':True}],
                       'hourly':[{'resource_id':'A','executed_revenue':600.,'revenue':None}]}}
    rows, delta = views.station_reconciliation(result,station)
    assert rows[0]['日前出力对应申报价']==85 and rows[0]['日前出力对应有效报价']==80
    assert rows[0]['实时价格影响 元']==200. and rows[1]['已执行现金 元']==0 and delta==0
    bridge = ('预期现金 元','日前数量影响 元','实时数量影响 元','实时价格影响 元')
    assert sum(r[k] for r in rows for k in bridge)==600.
    result['money']['quarters'][0].update(reliable=False)
    rows, delta = views.station_reconciliation(result,station)
    assert rows[0]['已执行现金 元'] is None and rows[0]['有效实时节点价'] is None and delta is None


def test_portfolio_adds_cash_and_values_to_receiving_station_only_on_valid_full_day(views):
    handoff = {'storage_m3':{'A':95,'B':230},'in_transit':[
        {'upstream':'A','downstream':'B','next_arrivals_m3':[15]}]}
    result = {'rt':[{'quarter':i} for i in range(96)],'summary':{'max_slack_mw':0},
              'inputs':{'stations':[{'id':'A','name':'上游'},{'id':'B','name':'下游'}],
                        'market_case':{'cascade':{'reservoirs':[{'id':'A','initial_storage_m3':100},
                                                               {'id':'B','initial_storage_m3':200}],
                              'reaches':[{'upstream':'A','downstream':'B','initial_arrivals_m3':[5]}]}}},
              'company':{'water_value_yuan_m3':{'A':2,'B':3}},
              'money':{'data_quality':'verified','portfolio_members':['A','B'],
                       'quarters':[{'reliable':True} for _ in range(96)],
                       'hourly':[{'resource_id':'A','energy_mwh':5,'executed_revenue':500},
                                 {'resource_id':'B','energy_mwh':0,'executed_revenue':0},
                                 {'resource_id':'COAL1','energy_mwh':100,'executed_revenue':1000}]}}
    result['rt'][-1]['water_handoff'] = handoff
    rows, totals = views.portfolio_reconciliation(result)
    assert len(rows)==2 and totals['cash']==500 and totals['energy_mwh']==5
    assert rows[0]['库水估值变化 元']==-10 and rows[0]['在途水估值变化 元']==0
    assert rows[1]['库水估值变化 元']==90 and rows[1]['在途水估值变化 元']==30
    assert rows[1]['实发加权价 元/MWh'] is None and totals['combined']==610
    result['money']['data_quality']='incomplete'
    result['money']['hourly'][0]['executed_revenue']=None
    _, sums = views.portfolio_reconciliation(result)
    assert sums['cash'] is None and sums['combined'] is None
    result['rt']=result['rt'][:4]
    result['money']['quarters']=result['money']['quarters'][:4]
    result['money']['data_quality']='verified'
    result['money']['hourly'][0]['executed_revenue']=500
    _, sums = views.portfolio_reconciliation(result)
    assert sums['cash']==500 and sums['water_change'] is None and sums['combined'] is None


def test_priced_subtotal_preserves_unverified_energy_and_missing_meter(views):
    result = {'money':{'quarters':[
            {'quarter':0,'reliable':True,'effective_price_by_bus':{1:100,2:None}},
            {'quarter':1,'reliable':False,'effective_price_by_bus':{1:950,2:85}},
            {'quarter':2,'reliable':True,'effective_price_by_bus':{1:200,2:40}}]},
        'rt':[{'quarter':0,'power':{'A':20,'B':0}},
              {'quarter':1,'power':{'A':10,'B':30}},
              {'quarter':2,'power':{'A':2,'B':4},'metered_mw':{'B':4}}]}
    coverage = views.priced_coverage(result,[{'id':'A','bus':1},{'id':'B','bus':2}])
    assert coverage == {'complete_quarters':1,'executed_quarters':3,'priced_mwh':6.,
                        'priced_cash':540.,'pending_mwh':10.,'missing_meters':1}
    # All suppressed intervals retain real executed energy; their absent cash stays missing.
    result['rt'] = result['rt'][1:2]
    all_invalid = views.priced_coverage(result,[{'id':'A','bus':1},{'id':'B','bus':2}])
    assert all_invalid['priced_cash'] is None and all_invalid['pending_mwh']==10.
