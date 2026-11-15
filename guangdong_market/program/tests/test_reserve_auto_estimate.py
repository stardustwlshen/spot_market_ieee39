import sys
from pathlib import Path
from decimal import Decimal as D
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
from gd_reserve_estimate import calculate

def fixture(power=40,online=1):
    h={'executed_cells':4,'rows':[{'resource':'C','technology':'coal'},{'resource':'PS','technology':'pumped_storage'}]}
    e={'rt_clock_mode':'provincial_15min_execution','cells':list(range(4)),'values':{**{f'power[C,{t}]':power for t in range(4)},**{f'online[C,{t}]':online for t in range(4)}}}
    c={'energy_inputs':{'horizon':{'periods':[{'duration_h':.25}]*4},'thermal':{'C':{'max_mw':dict.fromkeys(range(4),100)}}}}
    return h,e,c

def test_coal_floor_and_ps_exclusion():
    x=calculate(*fixture());assert D(x['total_CNY'])==1500;assert D(x['rows'][1]['amount_CNY'])==0

def test_pool_cap():
    x=calculate(*fixture(),{'compensation_pool_limit_mw':20});assert D(x['total_CNY'])==600

def test_offline_no_payment():assert D(calculate(*fixture(0,0))['total_CNY'])==0

def test_no_double_auction():
    with pytest.raises(ValueError,match='auction'):calculate(*fixture(),{'mode':'auction'})

def test_qualification_override():
    x=calculate(*fixture(),{'unit_overrides':{'C':{'qualified':False}}});assert D(x['total_CNY'])==0

def test_source_finer_grid():
    h,e,c=fixture();c['energy_inputs']['horizon']['periods']=[{'duration_h':1/12}]*12;c['energy_inputs']['thermal']['C']['max_mw']=dict.fromkeys(range(12),100)
    c['energy_inputs']['thermal']['C']['max_mw'][1]=80
    assert D(calculate(h,e,c)['total_CNY'])==1350
