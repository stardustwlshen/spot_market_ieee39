import sys,json
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
import pytest
import gd_service_estimates as s

def fixture(tmp_path):
    start='2026-11-11T00:00:00+08:00';end='2026-11-11T01:00:00+08:00'
    receipt={'schema':'gd.agc.hourly.research.v1','context':{'hour_start':start,'hour_end':end,'branch_id':'b'},'input':{'units':[{'resource_id':'C','k':2}]},'result':{'price':6,'unmet_mw':0,'hour_start':start,'binding_end':end,'awards_mw':{'C':10}}}
    p=tmp_path/'H00.json';p.write_text(json.dumps(receipt));b=tmp_path/'AGC_BINDING.json';b.write_text(json.dumps([{'run_id':'b','receipt_files':{str(p):s.sha(p)}}]))
    h={'run_id':'r','rows':[{'start':start,'end':end}]};e={'run_id':'r','source_pins':{str(b):s.sha(b)}};pins={}
    rows=s.collect(h,e,pins)
    return {'run_id':'r','source_manifest':pins,'agc_estimate_sources':rows},[(start,end)],p

def test_exact_formula_and_nonparticipant(tmp_path):
    v,p,_=fixture(tmp_path);r=s.estimate(v,p,['C','VRE'],10)
    assert r['C']['amount_CNY']=='1200.0'
    assert r['C']['actual_mileage_missing'] is True
    assert r['VRE']['amount_CNY']=='0'

def test_parameter_propagation(tmp_path):
    v,p,_=fixture(tmp_path)
    assert s.estimate(v,p,['C'],20)['C']['amount_CNY']=='2400.0'

def test_mixed_hour_rejected_not_filled(tmp_path):
    v,p,_=fixture(tmp_path)
    assert s.estimate(v,p+[('2026-11-11T01:00:00+08:00','2026-11-11T02:00:00+08:00')],['C'])=={}

def test_bad_binding_rejected(tmp_path):
    v,p,_=fixture(tmp_path);v['run_id']='other'
    with pytest.raises(ValueError):s.estimate(v,p,['C'])

def test_missing_actual_not_overwritten(tmp_path):
    from test_gd_default_costs import view
    import gd_economics_inputs as api
    v,p,_=fixture(tmp_path);v.update({k:x for k,x in view().items() if k not in ('source_manifest',)})
    v['source_manifest']['exec']='sha'
    out=api.calculate(v,api.defaults(v))['rows'][0]
    assert out['agc_revenue_CNY'] is None
    assert out['simulated_spot_profit_CNY'] is None
    assert out['agc_estimate']['amount_CNY']=='1200.0'
