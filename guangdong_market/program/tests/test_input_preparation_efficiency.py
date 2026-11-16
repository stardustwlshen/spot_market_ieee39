"""Input-transform contracts: aggregation, isolation and byte-key invalidation."""
from pathlib import Path
from copy import deepcopy
import sys,json,os
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
from gd_provincial15_model.compiler import dumps
from gd_provincial15_model.adapter import convert_case,convert_full_case
import gd_forecast_inputs as forecast


def case():
    return {'energy_inputs':{'horizon':{'periods':[{'index':t} for t in range(24)]},
                            'load_mw':{('A',t):float(t+1) for t in range(24)},
                            'renewable_mw':{('A',t):0. for t in range(24)}},
            'curves':{},'contract':{},'batteries':{},
            'battery_contract':{'storage':{'battery_parameters_source_id':'x','pumped_storage':{}},'sources':{'x':{}}}}

@pytest.mark.parametrize('field,expected',[('min_mw',3),('min_flow_mw',3),('minimum_mw',3),('max_mw',1),('max_flow_mw',1),('ramp_up_mw_per_h',1),('ramp_down_mw_per_h',1),('other',2)])
def test_aggregation(field,expected):
    c=case();c['test']={field:{('A',t):float(t+1) for t in range(24)}};before=deepcopy(c)
    result=convert_full_case(c)
    assert result['test'][field]['A',0]==expected
    assert c==before

@pytest.mark.parametrize('mode',['missing','extra','mixed'])
def test_nonrectangular_tuple_table_is_not_aggregated(mode):
    c=case();series={('A',t):float(t) for t in range(24)}
    if mode=='missing':series.pop(('A',7))
    elif mode=='extra':series['A',24]=24.
    else:series['note']='keep'
    c['test']=series;assert convert_full_case(c)['test']==series


def test_categories_boolean_and_alias_isolation():
    c=case();c['category']={t:{'x':[1]} for t in range(24)};c['flag']={t:t%3!=0 for t in range(24)}
    out=convert_full_case(c);assert out['flag']=={t:False for t in range(8)}
    out['category'][0]['x'].append(2);assert c['category'][0]['x']==[1]
    c['category'][1]={'x':[2]}
    with pytest.raises(ValueError,match='ambiguous categorical'):convert_full_case(c)


def test_boundary_and_horizon_rejection():
    b={'A':{'event_elapsed_ticks':3,'residual_up_ticks':4,'residual_down_ticks':7,'remaining_curve':[]}}
    _,out,_,_=convert_case(case(),b,{});assert out['A']==dict(event_elapsed_ticks=1,residual_up_ticks=2,residual_down_ticks=3,remaining_curve=[])
    assert b['A']['event_elapsed_ticks']==3
    b['A']['event_elapsed_ticks']=1
    with pytest.raises(ValueError,match='quarter-aligned'):convert_case(case(),b,{})
    with pytest.raises(ValueError,match='input cells'):convert_case(case(),{}, {},quarter_count=9)


def test_forecast_cache_is_full_content_keyed(tmp_path):
    forecast._baseline_digest_from_bytes.cache_clear();c=case();p=tmp_path/'case.json';p.write_bytes(dumps(c))
    z={'baseline_case_digest':forecast.baseline_digest(convert_full_case(c)),'notice':'test'};z['bundle_digest']=forecast.digest(z)
    f=tmp_path/forecast.FILE;f.write_text(json.dumps(z));first=forecast.read_bundle(tmp_path)
    assert forecast.read_bundle(tmp_path)==first
    assert forecast._baseline_digest_from_bytes.cache_info().hits==1
    first['notice']='mutated';assert forecast.read_bundle(tmp_path)['notice']=='test'
    stat=p.stat();c['energy_inputs']['load_mw']['A',0]+=1;p.write_bytes(dumps(c));os.utime(p,ns=(stat.st_atime_ns,stat.st_mtime_ns))
    with pytest.raises(ValueError,match='baseline does not match'):forecast.read_bundle(tmp_path)
    assert forecast._baseline_digest_from_bytes.cache_info().misses==2
    z['notice']='tamper';f.write_text(json.dumps(z))
    with pytest.raises(ValueError,match='bundle digest mismatch'):forecast.read_bundle(tmp_path)
