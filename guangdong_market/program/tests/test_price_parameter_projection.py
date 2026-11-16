"""Read-only internal projection. Source validation stays in the live caller."""
from pathlib import Path
from copy import deepcopy
import sys,json
import pytest
P=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(P/'app'),str(P/'app/gd_live_market_runtime'),str(P/'engines/guangdong_short_unified_v6/price')]
import branch_parameters as bp

@pytest.mark.parametrize('tick',[0,3,95])
def test_projection_keeps_bids_reserves_forecast_and_input(tmp_path,monkeypatch,tick):
    run=tmp_path/'run';(run/'seed').mkdir(parents=True);(run/'seed/case.json').write_text('{}')
    case={'energy_inputs':{'buses':['39'],'thermal':{'C1':{}},'renewable_bid_yuan_per_mwh':{('39',t):10.+t for t in range(96)}},'reserve':{'negative_mw':{t:0 for t in range(96)},'primary_mw':{t:20 for t in range(96)},'primary_fraction':{'C1':.05}},'_forecast_proof':{'issue_tick':tick},'research_curtailment_penalty_yuan_per_mwh':25.}
    before=deepcopy(case)
    def forbidden(*a,**k):raise AssertionError('redundant parse/conversion')
    monkeypatch.setattr(bp,'loads',forbidden);monkeypatch.setattr(bp,'convert_full_case',forbidden)
    request={'delivery_tick':tick,'origin':'2026-11-11T00:00:00+08:00','cutoff':'2026-11-11T00:00:00+08:00'}
    out=tmp_path/'params.json';bp._write_parameters(case,tmp_path,run,tick,out,'test','current_sced_research',verified_request=request)
    z=json.loads(out.read_text());count=min(8,96-tick)
    assert z['VRE_offers_by_tick_yuan_per_MWh']=={'39':[float(10+t)for t in range(tick,tick+count)]}
    assert z['constraints']['primary_frequency_reserve']['data']['requirement_MW']==[20]*count
    assert z['forecast_vintage']==case['_forecast_proof']
    assert z['penalties_yuan_per_MWh']['renewable_curtailment']==25
    assert case==before
    with pytest.raises(ValueError,match='fresh parameters'):bp._write_parameters(case,tmp_path,run,tick,out,'test','current_sced_research',verified_request=request)
