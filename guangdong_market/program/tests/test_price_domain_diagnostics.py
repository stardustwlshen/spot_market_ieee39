import sys
from pathlib import Path
import unittest
from contextlib import contextmanager
@contextmanager
def raises(error, match):
 try: yield
 except error as e:
  assert match in str(e)
 else: raise AssertionError("expected exception")
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
from gd_price_diagnostics import aggregate_balance_bound, thermal_reference_rows

def example():
 return ({'variables':[{'lb':0,'ub':90},{'lb':None,'ub':None},{'lb':0,'ub':None}], 'rows':[{'lb':40,'ub':40,'terms':[[0,1],[1,-1]]},{'lb':60,'ub':60,'terms':[[1,1],[2,1]]}]}, {'variables':['P[C1,0]','flow[L,0]','PRICE_load_shortage[0]'],'constraints':["balance['1',0]","balance['2',0]"]}, [90,50,10])
def test_cancel_network_and_prove_gap():
 m,n,x=example();r=aggregate_balance_bound(m,n,x)
 assert r['unavoidable_domain_gap_mw']==10
 assert r['balance_residual_mw']==0
 assert not r['physical_load_shedding_inferred']
def test_unbounded_nonnetwork_refused():
 m,n,x=example();m['variables'][0]['ub']=None
 with raises(ValueError,match='unbounded'):aggregate_balance_bound(m,n,x)
def test_uncancelled_flow_refused():
 m,n,x=example();m['rows'][1]['terms'][0][1]=.9
 with raises(ValueError,match='cancel'):aggregate_balance_bound(m,n,x)
def test_dimension_guard():
 m,n,x=example()
 with raises(ValueError,match='dimensions'):aggregate_balance_bound(m,n,x[:-1])
def test_reference_uses_result_not_current_default():
 c={'thermal_reference_extension':{'thermal_specs':{'P[C3,0]':dict(status='pricing_stable',da_sced=632.,current_sced=763.,da_max=855.,delta=.1),'P[C3,1]':dict(status='off')}}}
 r=thermal_reference_rows(c)[0]
 assert abs(r['定价允许上限（MW）']-695.2)<1e-8
 assert abs(r['调度超出定价上限（MW）']-67.8)<1e-8
def test_absent_context_not_filled():
 assert thermal_reference_rows({})==[]

if __name__ == "__main__":
 tests=[v for k,v in globals().copy().items() if k.startswith("test_")]
 for test in tests: test()
 print(f"{len(tests)} targeted tests passed")
