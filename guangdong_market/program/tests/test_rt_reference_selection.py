"""Focused stdlib checks: selectable current SCED reference, clocks and marginal space."""
from pathlib import Path
import sys,copy
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'app/gd_thermal_price_runtime'),str(ROOT/'app/gd_live_market_runtime')]
from thermal_reference import thermal_bounds, RT_POLICY, apply_to_serialized_model
from realtime_reference import assert_reference_alignment,preserve_current_ramps

def reject(call):
 try:call()
 except ValueError:return
 raise AssertionError('expected rejection')

def test_rt_reference_contract():
 spec=dict(status='pricing_stable',current_sced=763.,da_sced=632.,current_min=400.,current_max=855.,da_min=400.,da_max=855.,delta=.1)
 lo,hi=thermal_bounds(**spec,policy=RT_POLICY)
 assert lo<763<hi and abs(hi-839.3)<1e-9
 assert thermal_bounds(**spec)[1]<763 # old historical policy preserved
 changed=dict(spec,current_max=800.,da_max=855.)
 assert thermal_bounds(**changed,policy=RT_POLICY)[1]==800.
 assert thermal_bounds(**dict(spec,status='fixed_nonpricing'),policy=RT_POLICY)==(763.,763.)
 m={'variables':[{'kind':'C','lb':0.,'ub':855.}],'rows':[{'lb':0,'ub':1,'terms':[[0,1]]}],'objective':{'terms':[[0,100]]}}
 original=copy.deepcopy(m);updated,trace=apply_to_serialized_model(m,{'variables':['P[C3,0]']},{'P[C3,0]':spec},policy=RT_POLICY)
 assert original==m and updated['rows']==m['rows'] and updated['objective']==m['objective']
 s={'window':0,'request':{'delivery_tick':3},'values':{'power[C3,3]':760.,'power[C3,4]':763.,'power[C3,5]':766.}}
 state={'eligibility':{'C3':['pricing_stable']}}
 assert assert_reference_alignment(s,{'P[C3,0]':spec},state)
 reject(lambda:assert_reference_alignment(dict(s,window=-1),{'P[C3,0]':spec},state))
 wrong=copy.deepcopy(s);wrong['values']['power[C3,5]']=763.
 reject(lambda:assert_reference_alignment(wrong,{'P[C3,0]':spec},state))
 assert preserve_current_ramps(m,{}, {},policy='current_SCED_incoming_state')[0]==m
 print('9 RT reference and identity checks passed')
if __name__=="__main__":test_rt_reference_contract()

def test_live_entry_reference_import_and_mirror():
 """A clean interpreter must resolve the production path, not test path precedence."""
 import subprocess
 live=ROOT/'app/gd_live_market_runtime/thermal_reference.py'
 shared=ROOT/'app/gd_thermal_price_runtime/thermal_reference.py'
 assert live.read_bytes()==shared.read_bytes(), 'live thermal policy copy drifted'
 script="""
import sys
sys.path.insert(0,sys.argv[1])
import math_prepare
from thermal_reference import RT_POLICY,thermal_bounds
assert thermal_bounds(status='pricing_stable',current_sced=100.,da_sced=50.,current_min=0.,current_max=200.,da_min=0.,da_max=200.,delta=.1,policy=RT_POLICY)==(90.,110.00000000000001)
from realtime_reference import assert_reference_alignment,preserve_current_ramps
"""
 subprocess.run([sys.executable,'-c',script,str(live.parent)],check=True)
