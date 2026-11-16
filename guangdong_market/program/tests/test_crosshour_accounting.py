from pathlib import Path
import sys,json,hashlib,pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app/gd_restore_crosshour_runtime'))
from crosshour_accounting import verify_accounting

def fixture(tmp_path,w):
 p=tmp_path/'pipeline/price';p.mkdir(parents=True)
 hours=[]
 for h in range(3):
  z={'input_binding':{'hour':h},'executed_cells':4,'rows':[{'energy_amount_CNY':'12','components_CNY':{'DA':'10','RT_deviation':'2'}}],'total_energy_component_CNY':'12'}
  f=p/f'HOUR{h}.json';f.write_text(json.dumps(z));hours.append({'hour':h,'sha256':hashlib.sha256(f.read_bytes()).hexdigest()})
 prev={'settled_hours':hours[:2],'research_energy_total_CNY':'24','research_energy_components_CNY':{'DA':'20','RT_deviation':'4'}}
 n=2 if w<11 else 3
 ledger={'settled_hour_ids':list(range(n)),'unsettled_executed_ticks':list(range(8,w+1)) if w<11 else [],'included_ticks':[w],'next_execute_tick':w+1,'research_energy_total_CNY':str(n*12),'research_energy_components_CNY':{'DA':str(n*10),'RT_deviation':str(n*2)}}
 return prev,ledger

@pytest.mark.parametrize('w',[8,9,10,11])
def test_absolute_sum(tmp_path,w):
 prev,z=fixture(tmp_path,w);verify_accounting(tmp_path,w,z,prev)

@pytest.mark.parametrize('field,value',[('settled_hour_ids',[0,1,1,2]),('unsettled_executed_ticks',[11]),('included_ticks',[10,11]),('next_execute_tick',13),('research_energy_total_CNY','60'),('research_energy_components_CNY',{'DA':'29','RT_deviation':'7'})])
def test_corruption_rejected(tmp_path,field,value):
 prev,z=fixture(tmp_path,11);z[field]=value
 with pytest.raises(ValueError):verify_accounting(tmp_path,11,z,prev)

def test_old_hour_mutation(tmp_path):
 prev,z=fixture(tmp_path,11);p=tmp_path/'pipeline/price/HOUR1.json';p.write_text(p.read_text()+' ')
 with pytest.raises(ValueError,match='historical hour changed'):verify_accounting(tmp_path,11,z,prev)

from crosshour_accounting import verify_economics_arithmetic

def economic_row():
 return {'resource':'C1','spot_cost_CNY':'12','generation_cost_CNY':'10','executed_startup_cost_CNY':'2','energy_revenue_CNY':'20','energy_contribution_CNY':'8','agc_estimate':{'amount_CNY':'3'},'reserve_estimate':{'amount_CNY':'4'},'estimated_spot_margin_before_service_allocation_CNY':'15','service_allocation_included':False,'agc_revenue_CNY':None,'reserve_revenue_CNY':None,'simulated_spot_profit_CNY':None,'missing':['formal services missing']}

def test_economic_estimates_not_formal():
 r=economic_row();z=verify_economics_arithmetic({'unit_economics':{'rows':[r]}});assert not z['formal']

@pytest.mark.parametrize('field,value',[('spot_cost_CNY','10'),('energy_contribution_CNY','10'),('estimated_spot_margin_before_service_allocation_CNY','16'),('simulated_spot_profit_CNY','15'),('missing',[])])
def test_bad_economics(field,value):
 r=economic_row();r[field]=value
 with pytest.raises(ValueError):verify_economics_arithmetic({'unit_economics':{'rows':[r]}})

def test_historical_adapter_no_external_digest_import():
 source=(Path(__file__).resolve().parents[1]/'app/gd_restore_crosshour_runtime/adopt_frames.py').read_text()
 assert 'from domain_v23 import' not in source
 assert "sort_keys=True,separators=(',',':'),allow_nan=False" in source
