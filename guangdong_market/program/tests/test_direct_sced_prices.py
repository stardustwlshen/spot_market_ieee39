from pathlib import Path
import sys,copy
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
from gd_direct_sced_prices import extract,settle_executed_energy

def sample():
 names={'variables':[f"renewable_power['1',{t}]" for t in range(3)],'constraints':[f"c_balance['1',{t}]" for t in range(3)]}
 model={'objective':{'minimize':True},'variables':[{'kind':'C'} for _ in range(3)],'rows':[{'lb':s*10,'ub':s*10,'terms':[[t,s]]} for t,s in enumerate([-1,1,-1])]}
 response={'status':'optimal','dual_valid':True,'primal':[10]*3,'dual':[-10,20,-30]}
 kw=dict(duration_h={t:1/12 for t in range(3)},executed_ticks=[0,1,2],source_pins={'request':'0'*64})
 return model,names,response,kw

def test_sign_units_and_exact_settlement():
 m,n,r,kw=sample();p=extract(m,n,r,**kw)
 assert [p['five_minute_prices'][t]['1'] for t in range(3)]==[120,240,360]
 assert p['raw_prices']['1']==240
 assert settle_executed_energy(p,'1',{0:12,1:0,2:24})==840
 assert 240*(12+24)/12!=840
 assert p['additional_native_calls']==0

def test_reject_mip_invalid_dual_or_wrong_clock():
 m,n,r,kw=sample()
 m['variables'][0]['kind']='B'
 with pytest.raises(ValueError):extract(m,n,r,**kw)
 m['variables'][0]['kind']='C';r['dual_valid']=False
 with pytest.raises(ValueError):extract(m,n,r,**kw)
 r['dual_valid']=True;kw['executed_ticks']=[1,2,3]
 with pytest.raises(ValueError):extract(m,n,r,**kw)

def test_json_roundtrip_settlement():
 import json
 m,n,r,kw=sample();p=json.loads(json.dumps(extract(m,n,r,**kw)))
 assert settle_executed_energy(p,'1',{'0':12,'1':0,'2':24})==840
