"""Imported real hourly controller + file/subprocess + HiGHS micro dispatch.
Not a full provincial Pipeline/DA/SCED/PRICE acceptance test.
"""
import sys,json,tempfile,hashlib
from pathlib import Path
from types import SimpleNamespace
P=Path(__file__).resolve().parents[2]
PATCH=P/'app'
sys.path[:0]=[str(PATCH),str(P/'app'),str(P/'app/gd_live_market_runtime')]
import session
from scipy.optimize import linprog
from gd_balance_diagnostics import STATUS
_tmp=tempfile.TemporaryDirectory(prefix='market_slack_account_');ROOT=Path(_tmp.name)
# Producer test double uses actual native HiGHS solve and actual production classifier.
worker=ROOT/'micro_hour.py'
worker.write_text('''import sys,json
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from scipy.optimize import linprog
from gd_balance_diagnostics import classify,STATUS,attach_accounts
out=Path(sys.argv[2]);index=int(sys.argv[3]);load=100;cap=90 if index==0 else 120
r=linprog([10,100000],A_eq=[[1,1]],b_eq=[load],bounds=[(0,cap),(0,None)],method='highs')
assert r.success
power,slack=map(float,r.x);values={f'balance_shortfall[1,{t}]':slack for t in range(index*4,index*4+4)}
physical=[dict(tick=t,supply_MW=power,load_MW=load,residual_MW=power-load)for t in range(index*4,index*4+4)]
d=classify(physical,values)
z=dict(run_id='micro',physical_cells=physical,input_binding={'RT_physical_interval_minutes':15},rows=[{'simulated_executed_MWh':power,'energy_amount_CNY':power*10}])
attach_accounts(z,d,index)
z.setdefault('economic_status','available')
out.write_text(json.dumps(z,default=str))
print(json.dumps({'status':z['economic_status']}))
''')
real_call=session.call
with tempfile.TemporaryDirectory(dir=ROOT)as tmp:
 root=Path(tmp);q=root/'quarter.json';q.write_text('{}')
 digest=session.sha(q);counter={'hour':0}
 def verified():
  i=counter['hour'];return dict(executed_end=4*(i+1),issued_end=4*(i+1),values={},selected_DA='DA',branch_id='micro',branch='microbranch',base_run='BASE',request={'origin':'2026-01-01T00:00:00'},reports=[],source_pins={})
 snap=SimpleNamespace(verify=verified)
 host=SimpleNamespace(same_period_execution=True)
 obj=session.PriceSession.__new__(session.PriceSession)
 obj.windows=96;obj.host=host;obj.source=SimpleNamespace(snapshot=lambda _:snap);obj.accepted={i:(q,digest,snap)for i in range(8)};obj.execution={};obj.rt_clock_mode='provincial_15min_execution';obj.output=root
 settled=[];obj.settlement=lambda i:settled.append(i)or {'status':'micro_test_only'}
 # Do not patch the hourly controller, file writes, hashes, or subprocess launcher.
 session.call=lambda args,log:real_call([worker,PATCH,args[-1],counter['hour']],log)
 results=[]
 for i in range(2):
  counter['hour']=i;results.append(obj.hour(i))
 assert results[0]['status']=='simulation_accounts_with_balance_slack' and results[1]['status']=='available'
 from gd_settlement_bridge import from_export
 blocked=root/'blocked_for_import.json';blocked.write_text(json.dumps(dict(schema='gd.live_host.PRICE.hour.v3',run_id='micro',economic_status=STATUS,rows=[])))
 try:from_export(blocked,'micro',session.sha(blocked))
 except ValueError as ex:assert '不能作为零收入累计' in str(ex)
 else:raise AssertionError('abnormal hour accepted as monetary ledger')
 assert settled==[0,1] and(root/'SETTLEMENT_H0.json').exists() and(root/'SETTLEMENT_H1.json').exists()
 for f in root.iterdir():
  if f.is_file():(ROOT/f.name).write_bytes(f.read_bytes())
 session.call=lambda *args:(_ for _ in ()).throw(ValueError('unrelated source verification error'))
 obj.execution={};counter['hour']=0
 # Use fresh output directory so unrelated error reaches the call boundary.
 obj.output=root/'other';obj.output.mkdir()
 try:obj.hour(0)
 except ValueError as e:assert str(e)=='unrelated source verification error'
 else:raise AssertionError('nonbalance error swallowed')
 summary=dict(passed=True,real_module=session.__file__,actual_solver='scipy.optimize.linprog method highs',hours=results,settled_hours=settled,full_production_pipeline=False,test_doubles=['snapshot and accepted quarter identity fixture','hour producer replaced by native micro dispatch plus production classify','normal-hour settlement arithmetic not repeated'],files={f.name:hashlib.sha256(f.read_bytes()).hexdigest()for f in ROOT.glob('*.json') if f.name!='TEST_RESULT.json'})
 (ROOT/'TEST_RESULT.json').write_text(json.dumps(summary,indent=2));print(json.dumps(summary))
