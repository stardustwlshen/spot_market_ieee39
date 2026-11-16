from pathlib import Path
import json,sys,pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
from gd_service_estimates import collect,sha

def data(tmp_path):
 start='2026-11-11T02:00:00+08:00';end='2026-11-11T03:00:00+08:00'
 receipt={'schema':'gd.agc.hourly.research.v1','context':{'hour_start':start,'hour_end':end,'branch_id':'agc-branch'},'result':{'price':12,'unmet_mw':0,'hour_start':start,'binding_end':end,'awards_mw':{'C1':10}},'input':{'units':[{'resource_id':'C1','k':1}]}}
 f=tmp_path/'H02.json';f.write_text(json.dumps(receipt));pins={}
 for q in range(4):
  p=tmp_path/f'W{q+8}'/'AGC_BINDING.json';p.parent.mkdir();stamp=f'2026-11-11T02:{q*15:02d}:00+08:00';p.write_text(json.dumps([{'domain_start':stamp,'cutoff':stamp,'run_id':'agc-branch','receipt_files':{str(f):sha(f)}}]));pins[str(p)]=sha(p)
 h={'run_id':'rt-branch','rows':[{'start':start,'end':end}]};e={'rt_clock_mode':'provincial_15min_execution','run_id':'rt-branch','origin':'2026-11-11T00:00:00+08:00','hour':2,'cells':[8,9,10,11],'source_pins':pins}
 return h,e,f

def test_irrelevant_historical_locator_not_required(tmp_path):
 h,e,f=data(tmp_path);p=tmp_path/'old'/'AGC_BINDING.json';p.parent.mkdir();p.write_text(json.dumps([{'domain_start':'2026-11-11T00:00:00+08:00','cutoff':'2026-11-11T00:00:00+08:00','receipt_files':{'/absent/old':'0'*64}}]));e['source_pins'][str(p)]=sha(p)
 assert collect(h,e,{})[0]['awards_mw']=={'C1':10}

@pytest.mark.parametrize('fault',['missing_binding','changed_binding','missing_receipt','changed_receipt','wrong_cells','duplicate_binding','wrong_branch','wrong_time'])
def test_current_evidence_fails_closed(tmp_path,fault):
 h,e,f=data(tmp_path);p=Path(next(iter(e['source_pins'])))
 if fault=='missing_binding':e['source_pins'].pop(str(p))
 elif fault=='changed_binding':p.write_text('[]')
 elif fault=='missing_receipt':f.unlink()
 elif fault=='changed_receipt':f.write_text('{}')
 elif fault=='wrong_cells':e['cells']=[4,5,6,7]
 elif fault=='duplicate_binding':
  a=json.loads(p.read_text());p.write_text(json.dumps(a+a));e['source_pins'][str(p)]=sha(p)
 else:
  a=json.loads(f.read_text());a['context']['branch_id' if fault=='wrong_branch' else 'hour_end']='wrong';f.write_text(json.dumps(a))
  for x in e['source_pins']:
   b=json.loads(Path(x).read_text());b[0]['receipt_files']={str(f):sha(f)};Path(x).write_text(json.dumps(b));e['source_pins'][x]=sha(x)
 with pytest.raises((ValueError,FileNotFoundError)):collect(h,e,{})
