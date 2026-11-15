import sys,pathlib
sys.path.insert(0,str(pathlib.Path(__file__).parents[1]/'app'))
from gd_network_violations import summarize

def test_violation_not_reported_secure():
 z=summarize({'variables':['line_over[L16-19,80]','section_under[S1,96]']},{'primal':[13.2,2.]})
 assert z['has_violation'] and not z['network_secure']
 assert z['delivery_violation_count']==1 and z['representative_violation_count']==1
 assert z['maximum_excess_mw']==13.2

def test_zero_slack_not_false_alarm():
 assert not summarize({'variables':['line_over[L1,0]']},{'primal':[1e-9]})['has_violation']

def test_dispatch_penalties_do_not_override_balance_or_reserve():
 from gd_provincial15_model.penalty_policy import apply_dispatch
 c={'energy_inputs':{'penalties_yuan_per_mwh':{'line':1000.,'section':1000.,'balance_shortfall':10000.,'balance_surplus':10000.,'reserve':100000.},'source_notes':{}},'reserve':{'positive_mw':{0:400.}}}
 apply_dispatch(c);p=c['energy_inputs']['penalties_yuan_per_mwh']
 assert p['line']==p['section']==10000
 assert p['balance_shortfall']==10000 and p['reserve']==100000
 assert c['reserve']['positive_mw'][0]==400
