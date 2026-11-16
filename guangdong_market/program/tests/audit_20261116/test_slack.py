import sys,ast,json,tempfile
from pathlib import Path
from types import SimpleNamespace
P=Path(__file__).resolve().parents[2]/'app';sys.path.insert(0,str(P))
from gd_balance_diagnostics import classify,STATUS
from gd_full_day_ui import economic_total
assert classify([{'tick':0,'residual_MW':0}],{}) is None
assert classify([{'tick':0,'residual_MW':-10}],{'balance_shortfall[1,0]':10})[0]['shortfall_MW']==10
assert classify([{'tick':0,'residual_MW':10}],{'balance_surplus[1,0]':10})[0]['surplus_MW']==10
assert classify([{'tick':0,'residual_MW':0}],{'balance_shortfall[1,0]':10,'balance_surplus[2,0]':10})
for values in ({},{'balance_shortfall[1,0]':9},{'balance_shortfall[1,0]':float('nan')}):
 try:classify([{'tick':0,'residual_MW':-10}],values)
 except ValueError:pass
 else:raise AssertionError('unexplained errors must propagate')
try:economic_total([({'economic_status':STATUS},{})])
except ValueError as ex:assert '完整累计' in str(ex)
else:raise AssertionError('unknown economics not zero')
for pairs in ([({'economic_status':STATUS},{}),({}, {})],[({},{}),({'economic_status':STATUS},{})]):
 try:economic_total(pairs)
 except ValueError as ex:assert '完整累计' in str(ex)
 else:raise AssertionError('mixed hours cannot publish full total')
# Execute unchanged production PriceSession.hour method with source/IO test doubles.
tree=ast.parse((P/'gd_live_market_runtime/session.py').read_text()); cls=next(x for x in tree.body if isinstance(x,ast.ClassDef) and x.name=='PriceSession'); method=next(x for x in cls.body if isinstance(x,ast.FunctionDef) and x.name=='hour')
import re
with tempfile.TemporaryDirectory()as tmp:
 root=Path(tmp); results={}; settled=[]
 def need(ok,msg):
  if not ok:raise ValueError(msg)
 z=dict(executed_end=4,issued_end=4,values={},selected_DA='DA',branch_id='RUN',branch='BRANCH',base_run='BASE',request={'origin':'2026-01-01T00:00:00'},reports=[],source_pins={})
 snap=SimpleNamespace(verify=lambda:z)
 holder=SimpleNamespace(windows=8,host=SimpleNamespace(same_period_execution=True),source=SimpleNamespace(snapshot=lambda _:snap),accepted={0:('q','hash',snap)},execution={},rt_clock_mode='provincial_15min_execution',output=root,settlement=lambda i:settled.append(i) or {})
 def call(args,log):
  out=args[-1];results[out]={'economic_status':STATUS} if out.name=='HOUR0.json' else {};return {'status':results[out].get('economic_status','accepted')}
 env=dict(hour_scope=lambda *a,**k:dict(source_windows=[0],handoff_window=0,executed_end=4,issued_end=4,cells=[],excluded_issued_ticks=[]),e=SimpleNamespace(need=need),re=re,write=lambda p,v:results.update({p:v}),B=root,call=call,sha=lambda _:'hash',read=lambda p:results[p])
 exec(compile(ast.Module(body=[method],type_ignores=[]),'session.hour','exec'),env)
 assert env['hour'](holder,0)['status']==STATUS
 assert env['hour'](holder,1)['status']=='accepted'
 assert settled==[1] and len(holder.execution)==2
 # Other failures remain failures, not classified as settlement-unavailable.
 holder.execution={};env['call']=lambda *a:(_ for _ in ()).throw(ValueError('PRICE provenance mismatch'))
 try:env['hour'](holder,0)
 except ValueError as ex:assert str(ex)=='PRICE provenance mismatch'
 else:raise AssertionError('nonbalance failure swallowed')
print(json.dumps({'passed':True,'coverage':['balanced','shortfall','surplus','offsetting nodal slacks','unknown imbalance rejected','nonfinite rejected','unknown totals blocked','abnormal hour then normal hour continues','nonbalance call error propagates'],'scope':'production helper and extracted production hour method; source/IO test doubles, not full solver run'}))
