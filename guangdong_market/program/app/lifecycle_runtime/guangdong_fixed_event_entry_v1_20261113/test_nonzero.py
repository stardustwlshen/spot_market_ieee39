from event_path import *
import unittest,re
B=Path(__file__).resolve().parent;R=B.parent
sys.path.insert(0,str(R/'guangdong_thermal_notice_bridge_v2_20261113'))
import test_bridge as old
# Existing host bridge independently checks full accepted pair and typed curve pins.
plan=old.load_plan(old.PAIR,old.sha(old.PAIR),old.CONTEXT,old.PARAM);a=old.read(old.PAIR);paths=a['paths'];q,u,eq,e,n=[old.read(paths[k])for k in ('SCUC_request','SCUC_response','SCED_request','SCED_response','names')]
ledger0=old.publish_due(plan,None,cutoff_tick=0,simulate_publish=True)
pre=old.connect_boundary(old.initial(),old.PARAM,ledger0,old.cells(0,6),cutoff_tick=6,executed_cells=6,origin=old.ORIGIN,source='existing accepted pair')
def bundle(begin,state,count=3):
 boundary=old.to_short_boundary(dict(executed_boundary=state,predicted_issued_boundary=state,events=ledger0['receipts']),predicted=False,available_at=old.ORIGIN,source='host verified accepted R68 prefix')
 case={'contract':dict(origin=old.ORIGIN,domain_start=(stamp(old.ORIGIN)+timedelta(minutes=5*begin)).isoformat(),cutoff=old.ORIGIN),'energy_inputs':{'horizon':{'periods':[{}for _ in range(count)]},'thermal':{'G':{'min_up_h':0,'min_down_h':0,'initial_state_age_h':state['connection_state_age_ticks']/12}}},'curves':{'G':{'startup':old.PARAM['classes']['warm']['rows'],'shutdown':old.PARAM['shutdown']['rows'],'notice_h':old.PARAM['classes']['warm']['notice_ticks']/12}},'batteries':{}}
 nn=deepcopy(n)
 for i,name in enumerate(nn['variables']):
  m=re.fullmatch(r'(power|start|stop|online|stable)\[G,(\d+)\]',name)
  if m:nn['variables'][i]=f'{m[1]}[G,{int(m[2])-begin}]'
 return dict(schema='gd.fixed.host_bundle.v1',mode='fixed_curve_research',run_id='R68-fixed-view-replay',case_identity=plan['parameters_sha256'],available_at=old.ORIGIN,case=pack(case),boundary=pack({'thermal':boundary['thermal']}),SCUC_request=q,SCUC_response=u,SCED_request=eq,SCED_response=e,names=nn,host_source_selection={'pair_digest':old.sha(old.PAIR),'scope':'bounded fixed warm event view of verified real R68 pair; not IEEE39 typed parameter substitution'})
first=bundle(6,pre['carry']['executed_boundary']);ledger=publish(first,None,expected_bundle_sha256=digest(first),cutoff_tick=0,simulate=True)
c1=carry_candidate(first,ledger,expected_bundle_sha256=digest(first),expected_ledger_sha256=ledger["ledger_sha256"]);actual=old.connect_boundary(pre['carry']['executed_boundary'],old.PARAM,ledger0,old.cells(6,9),cutoff_tick=9,executed_cells=3,origin=old.ORIGIN,source='existing accepted pair');second=bundle(9,actual['carry']['executed_boundary']);c2=carry_candidate(second,ledger,expected_bundle_sha256=digest(second),expected_ledger_sha256=ledger["ledger_sha256"])
for b in (first,second):
 built=build_candidate(b,ledger,expected_bundle_sha256=digest(b),expected_ledger_sha256=ledger["ledger_sha256"]);x=e['primal']
 for row in built['template']['rows'][len(q['rows']):]:assert abs(math.fsum(v*x[j]for j,v in row['terms'])-row['lb'])<1e-7
assert c1['boundary_states']['G']['phase']=='startup'and c2['boundary_states']['G']['phase']=='stable';assert c1['thermal_age_h']['G']==.25 and c2['thermal_age_h']['G']==.5
class T(unittest.TestCase):
 def test_host_digest_drift(self):
  z=deepcopy(first);z['available_at']='2026-11-11T00:05:00+08:00'
  with self.assertRaises(ValueError):export_plan(z,expected_bundle_sha256=digest(first))
 def test_false_raw_acceptance(self):
  z=deepcopy(first);z['SCED_response']['primal'][0]+=1
  with self.assertRaises((ValueError,AssertionError)):export_plan(z,expected_bundle_sha256=digest(z))
 def test_wrong_clock(self):
  z=deepcopy(ledger);z['origin']='2026-11-12T00:00:00+08:00';z['ledger_sha256']=digest({k:v for k,v in z.items()if k!='ledger_sha256'})
  with self.assertRaises(ValueError):carry_candidate(first,z,expected_bundle_sha256=digest(first),expected_ledger_sha256=z["ledger_sha256"])
 def test_future_publication(self):
  z=deepcopy(first);z['available_at']='2026-11-11T00:05:00+08:00'
  with self.assertRaises(ValueError):publish(z,None,expected_bundle_sha256=digest(z),cutoff_tick=0,simulate=True)
 def test_late_notice(self):
  with self.assertRaises(ValueError):publish(first,None,expected_bundle_sha256=digest(first),cutoff_tick=1,simulate=True)
 def test_missing_notice(self):
  z=deepcopy(ledger);z['receipts']={};z['ledger_sha256']=digest({k:v for k,v in z.items()if k!='ledger_sha256'})
  with self.assertRaises(AssertionError):carry_candidate(first,z,expected_bundle_sha256=digest(first),expected_ledger_sha256=z["ledger_sha256"])
 def test_no_optin(self):
  with self.assertRaises(ValueError):publish(first,None,expected_bundle_sha256=digest(first),cutoff_tick=0)
 def test_future_pending_replace(self):
  z=deepcopy(first);case=unpack(z['case']);case['curves']['G']['notice_h']=0.;z['case']=pack(case)
  pending=publish(z,None,expected_bundle_sha256=digest(z),cutoff_tick=0,simulate=True);self.assertEqual(len(pending['pending']),1)
  # New accepted later view contains no startup, so not-yet-issued forecast disappears.
  nxt=publish(second,pending,expected_bundle_sha256=digest(second),cutoff_tick=0,simulate=True,expected_previous_ledger_sha256=pending["ledger_sha256"]);self.assertFalse(nxt['pending'])
 def test_issued_curve_change(self):
  z=deepcopy(first);case=unpack(z['case']);case['curves']['G']['startup'][0]['mean']+=1;z['case']=pack(case)
  with self.assertRaises(ValueError):publish(z,ledger,expected_bundle_sha256=digest(z),cutoff_tick=0,simulate=True,expected_previous_ledger_sha256=ledger["ledger_sha256"])
if __name__=='__main__':
 t=unittest.main(exit=False,verbosity=2).result
 need(t.wasSuccessful(),'tests failed');(B/'NONZERO_RESULT.json').write_text(json.dumps(dict(tests=t.testsRun,passed=True,new_native_calls=0,existing_R68_real_pair_reused=True,first_phase=c1['boundary_states']['G']['phase'],second_phase=c2['boundary_states']['G']['phase'],no_IEEE39_typed_parameters_invented=True),indent=2))
