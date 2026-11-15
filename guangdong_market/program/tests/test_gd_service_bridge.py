import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
from gd_settlement_bridge import components

class Session:
    digest='hoursha'
    h={'run_id':'r','rows':[dict(resource='C',technology='coal',node='1',direction='discharge',start='2026-11-11T00:00:00+08:00',end='2026-11-11T01:00:00+08:00',energy_amount_CNY='10000',simulated_executed_MWh='10')]}
    def check(self):pass
    def energy(self,f):return {'status':'missing'}
    non_da_renewable=operating_compensation=energy
    def agc(self,f):
        if not f:return {'status':'missing'}
        return {'binding':{'run_id':'r','hour_sha256':'hoursha'},'component':{'reconciled_research_yuan':f['amount']}}
    def reserve(self,f):
        if not f:return {'status':'missing'}
        return {'binding':{'run_id':'r','hour_sha256':'hoursha'},'component':{'rows':[{'resource':'C','amount_CNY':'200'}]}}

def test_computed_services_feed_profit_without_hand_totals():
    result=components(Session(),{'agc_by_resource':{'C':{'amount':'100'}},'reserve':{'configured':True},'costs':{'C':{'source':'assumption','generation_CNY_MWh':'300','executed_startup_cost_CNY':'0'}}})
    row=result['unit_economics']['rows'][0]
    assert row['simulated_spot_profit_CNY']=='7300'
    assert result['agc_by_resource']['C']['component']['resource']=='C'

def test_unknown_resource_rejected():
    result=components(Session(),{'agc_by_resource':{'wrong':{'amount':'100'}}})
    assert result['agc_by_resource']['wrong']['status']=='input_rejected'
    assert result['unit_economics']['rows'][0]['agc_revenue_CNY'] is None
