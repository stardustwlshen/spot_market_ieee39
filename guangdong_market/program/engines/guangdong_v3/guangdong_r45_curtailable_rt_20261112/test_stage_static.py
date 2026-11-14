"""Small same-stage hash/unchanged-authentication tests. No optimizer/native."""
from pathlib import Path
from copy import deepcopy
from hashlib import sha256
import ast,os,sys
import pytest
from r45smm import gd_r41_static_stage as g
from r45smm import gd_r45_source_contract as c
BASE=Path(__file__).resolve().parent;OLD=BASE.parent/'guangdong_r37_new_da_rt_entry_20261112'
FORBIDDEN=[]
def guard(event,args):
 if event in ('subprocess.Popen','os.system','os.posix_spawn','socket.connect','socket.bind')or(event=='import'and str(args[0]).split('.')[0]in ('highspy','coptpy','gurobipy')):
  FORBIDDEN.append(event);raise RuntimeError('test forbids optimizer/process/network')
sys.addaudithook(guard)
@pytest.fixture
def f(tmp_path,monkeypatch):
 path=tmp_path/'source';path.write_bytes(b'original source');files={str(path):sha256(path.read_bytes()).hexdigest()};calls=[];old=g._hash_file
 def counted(p):calls.append(p);return old(p)
 monkeypatch.setattr(g,'_hash_file',counted)
 return path,files,calls

def test_nine_requests_become_two_checks_per_stage(f):
 path,files,calls=f;p=object()
 for stage,count in [('RT',4),('PRICE',5)]:
  with g.scope(p,files,run_id='fresh',index=0,stage=stage)as r:
   for _ in range(count):g.verify(p,files)
  assert r['completed']and r['reused_in_stage_checks']==count and r['entry']['files']==r['exit']['files']==1
 assert len(calls)==4 and g._CURRENT.get()is None

def test_outside_scope_never_cached(f):
 _,files,calls=f;p=object();g.verify(p,files);g.verify(p,files);assert len(calls)==2

def test_foreign_permit_does_not_reuse(f):
 _,files,calls=f
 with g.scope(object(),files,run_id='a',index=0,stage='RT')as r:g.verify(object(),files);assert r['reused_in_stage_checks']==0
 assert len(calls)==3

def test_preentry_change_rejected_before_body(f):
 path,files,_=f;path.write_bytes(b'different');entered=False
 with pytest.raises(ValueError,match='bound file changed'):
  with g.scope(object(),files,run_id='a',index=0,stage='RT'):entered=True
 assert not entered and g._CURRENT.get()is None

def test_during_stage_change_preserving_mtime_rejected_before_accept(f):
 path,files,_=f;before=path.stat();accepted=False
 with pytest.raises(ValueError,match='bound file changed'):
  with g.scope(object(),files,run_id='a',index=0,stage='RT'):
   path.write_bytes(b'altered! source');os.utime(path,ns=(before.st_atime_ns,before.st_mtime_ns))
  accepted=True
 assert not accepted and g._CURRENT.get()is None

def test_inventory_mutation_rejected(f):
 _,files,_=f
 with pytest.raises(ValueError,match='inventory changed'):
  with g.scope(object(),files,run_id='a',index=0,stage='RT'):files['other']='0'*64
 assert g._CURRENT.get()is None

def test_nested_stage_rejected(f):
 _,files,_=f;p=object()
 with g.scope(p,files,run_id='a',index=0,stage='RT'):
  with pytest.raises(ValueError,match='nested'):
   with g.scope(p,files,run_id='b',index=0,stage='PRICE'):pass

def test_exception_still_hashes_exit_and_clears(f):
 _,files,calls=f;p=object()
 with pytest.raises(RuntimeError,match='original failure'):
  with g.scope(p,files,run_id='a',index=0,stage='RT'):raise RuntimeError('original failure')
 assert len(calls)==2 and g._CURRENT.get()is None
 g.verify(p,files);assert len(calls)==3

def test_second_stage_requires_new_real_check(f):
 path,files,calls=f;p=object()
 with g.scope(p,files,run_id='a',index=0,stage='RT'):pass
 path.write_bytes(b'changed between stages')
 with pytest.raises(ValueError,match='bound file changed'):
  with g.scope(p,files,run_id='a',index=0,stage='PRICE'):pass
 assert len(calls)==3

@pytest.mark.parametrize('field',['contract','source','data'])
def test_memory_seal_checks_not_cached(f,field):
 _,files,_=f;p=c.ContractPermit();plan=dict(policy=deepcopy(c.POLICY),physical_cells=[{}for _ in range(309)],references=[{}for _ in range(103)])
 source={'x':1};data={'y':2};e=dict(sha=c.digest(plan),source=source,source_sha=c._source_digest(source),data=data,data_sha=c.digest(data),files=files);c._CONTRACTS[p]=e
 with pytest.raises(ValueError,match='mutated'):
  with c.native_stage_scope(plan,p,run_id='test',index=0,stage='RT'):
   if field=='contract':plan['physical_cells'][0]['changed']=True
   elif field=='source':source['x']=2
   else:data['y']=3
   c.validate_contract(plan,p,recheck_files=True)
 assert g._CURRENT.get()is None


def function(path,name):
 tree=ast.parse(path.read_text());return next(z for z in tree.body if isinstance(z,(ast.FunctionDef,ast.AsyncFunctionDef))and z.name==name)

def test_native_ticket_clock_stop_and_option_functions_exact_unchanged():
 pairs={'gd_r45_priced_native_adapter.py':['_solve_stage_impl','_solve_stage','_validate_stage_ticket','_validate_occurrence','validate_release','authenticate_projection','_advance_to_impl','_stop_run','failure_snapshot'],'gd_r45_day_session.py':['_session','_ticket','prepare_window','validate_window_input'],'gd_r45_rt_pricing.py':['_price_ticket','validate_price_inputs']}
 for file,names in pairs.items():
  for name in names:assert ast.dump(function(BASE/'r45smm'/file,name),include_attributes=False)==ast.dump(function(OLD/'r45smm'/file,name),include_attributes=False)

def test_scope_exits_precede_capability_and_command_publication():
 f=function(BASE/'r45smm/gd_r45_priced_native_adapter.py','run_priced_window');body=next(z for z in f.body if isinstance(z,ast.Try)).body
 scopes=[(i,z)for i,z in enumerate(body)if isinstance(z,ast.With)];assert len(scopes)==2
 rtpos,rt=scopes[0];pricepos,price=scopes[1]
 assert any(isinstance(z,ast.Assign)and any(isinstance(t,ast.Subscript)and isinstance(t.value,ast.Name)and t.value.id=='_RT'for t in z.targets)for z in body[rtpos+1:pricepos])
 for scope in (rt,price):
  assert not any(isinstance(z,ast.Call)and isinstance(z.func,ast.Name)and z.func.id in ('AcceptedRTWindow','AcceptedPriceWindow')for z in ast.walk(scope))
  assert not any(isinstance(z,ast.Constant)and isinstance(z.value,str)and 'issued_commands.json'in z.value for z in ast.walk(scope))
 assert not FORBIDDEN
