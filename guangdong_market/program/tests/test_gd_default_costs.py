import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
import pytest
from decimal import Decimal
from gd_default_costs import profile
import gd_economics_inputs as api

def view():
    period=('2026-11-11T00:00:00+08:00','2026-11-11T01:00:00+08:00')
    rows=[dict(resource='C',node='1',direction='discharge',technology='coal',start=period[0],end=period[1],simulated_executed_MWh='100',energy_amount_CNY='40000',capacity_mw='600')]
    return dict(run_id='r',settlement_hour_rows=rows,source_manifest={'exec':'sha'},execution_event_summaries=[dict(run_id='r',start=period[0],end=period[1],source_path='exec',source_sha256='sha',rows=[dict(resource='C',start_count=0,stop_count=0,missing=[])])])

def test_type_capacity_costs():
    assert profile('coal',600)['generation_CNY_MWh']=='280'
    assert profile('coal',200)['generation_CNY_MWh']=='330'
    assert profile('gas',400)['generation_CNY_MWh']=='500'
    assert profile('independent_storage')['generation_CNY_MWh']=='0'
    assert profile('unknown') is None

def test_default_margin_not_fake_full_profit():
    v=view();r=api.defaults(v);out=api.calculate(v,r)['rows'][0]
    assert out['generation_cost_CNY']==Decimal('28000')
    assert out['executed_startup_cost_CNY']==0
    assert out['energy_contribution_CNY']==12000
    assert out['simulated_spot_profit_CNY'] is None

def test_executed_start_only():
    v=view();v['execution_event_summaries'][0]['rows'][0]['start_count']=1
    assert api.defaults(v)['units']['C']['executed_startup_cost_CNY']=='100000'

def test_wrong_source_not_zero():
    v=view();v['source_manifest']['exec']='other'
    assert api.defaults(v)['units']['C']['executed_startup_cost_CNY'] is None

def test_missing_events_not_zero():
    v=view();v['execution_event_summaries']=[]
    assert api.defaults(v)['units']['C']['executed_startup_cost_CNY'] is None

def test_user_override_survives_save_load(tmp_path):
    v=view();r=api.defaults(v);r['units']['C']['generation_CNY_MWh']='321'
    api.save(v,r,tmp_path)
    assert api.load(v,tmp_path)['units']['C']['generation_CNY_MWh']=='321'

def test_no_double_charge_cost():
    v=view();v['settlement_hour_rows'][0].update(technology='independent_storage',direction='charge',simulated_executed_MWh='-100',energy_amount_CNY='-40000')
    r=api.defaults(v);out=api.calculate(v,r)['rows'][0]
    assert out['generation_cost_CNY']==0
    assert out['energy_contribution_CNY']==-40000

def test_duplicate_events_rejected():
    v=view();v['execution_event_summaries']*=2
    with pytest.raises(ValueError):api.defaults(v)
