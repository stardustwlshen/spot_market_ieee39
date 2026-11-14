from pathlib import Path
import json,sys,hashlib,dataclasses
import pyomo.environ as p
from startup import *
B=Path(__file__).resolve().parent;ROOT=B.parent
sys.path.insert(0,str(ROOT/'guangdong_after_reset_r23_compressed_20261112/repo/src'))
from smm.highs_solver import serialize_model,validate_options

def fixture():
 def curve(vals,fee,lead):
  ends=tuple(vals);begins=(0.,)+ends[:-1];return Curve(tuple((a+b)/2 for a,b in zip(begins,ends)),begins,ends,fee,lead,'R65 explicitly synthetic curve/fee/notice fixture')
 return StartupDTO('research','R65 independent toy, not IEEE39/Guangdong operating data',15,'disconnect_to_start_curve_begin','Synthetic boundaries4ticks/8ticks; NOT10h/72h',4,8,'off',3,'Synthetic offline age45min at horizon begin',None,None,'not an authenticated quote limit',{'hot':curve((100.,),900.,1),'warm':curve((50.,100.),200.,2),'cold':curve((25.,60.,100.),1.,3)},Curve((75.,25.),(100.,50.),(50.,0.),7.,0,'Synthetic shutdown2ticks, disconnection at end'),classification_boundary_policy='hot_lt_H_warm_lt_W')
def build():
 d=fixture();m=p.ConcreteModel();b=attach(m,'unit',d,30)
 for t in b.T:b.stop[t].fix(int(t in(6,14,28)))
 m.service=p.ConstraintList()
 for t in(4,12,27):m.service.add(b.stable[t]==1)
 for t in(8,16,23):m.service.add(b.off[t]==1)
 m.service.add(sum(b.start[t]for t in b.T)==3);m.cost=p.Objective(expr=b.event_fee)
 return m,d

def known_witness(m,d):
 b=m.unit;starts={2:'warm',10:'hot',24:'cold'};stops={6,14,28};offline=d.initial_offline_ticks
 for t in b.T:
  b.start[t].value=int(t in starts);b.stop[t].value=int(t in stops)
  for k in CLASSES:b.kind[t,k].value=int(starts.get(t)==k);b.notice[t,k].value=int(starts.get(t+d.curves[k].notice_lead_ticks)==k)
 for t in b.B:
  active=any(s<=t<s+len(d.curves[k].mean_mw)for s,k in starts.items())or any(s<=t<s+2 for s in stops)
  stable=any(s+len(d.curves[k].mean_mw)<=t<next(x for x in sorted(stops)if x>s)for s,k in starts.items())
  b.stable[t].value=int(stable);b.off[t].value=int(not active and not stable)
  if t:
   if t-2 in stops:offline=0
   elif p.value(b.off[t-1]):offline+=1
   else:offline=0
  b.offline_ticks[t].value=offline
 return starts

def main():
 out=B/'prepared_v2';out.mkdir(exist_ok=False);m,d=build();q,vs,cs=serialize_model(m,validate_options({'time_limit':30.,'threads':1,'mip_rel_gap':.001,'mip_abs_gap':0.,'output_flag':False}));names={'variables':[v.name for v in vs],'constraints':[c.name for c in cs]};known_witness(m,d)
 from smm.gd_r23_equivalent_compression import audit
 witness=[p.value(v)for v in vs];proof=audit(q,witness,1122.)
 for n,x in [('template.json',q),('names.json',names),('DTO.json',dataclasses.asdict(d)),('KNOWN_FEASIBLE_WITNESS.json',{'values':witness,'audit':proof,'source':'explicit constructed toy witness, NOT native solution','native_calls':0})]:
  (out/n).write_text(json.dumps(x,indent=2,allow_nan=False))
 pin=lambda x:hashlib.sha256(x.read_bytes()).hexdigest()
 ready={'native_calls':0,'variables':len(vs),'rows':len(cs),'expected_optimum_event_fee':1122,'fixed_stop_begin_ticks':[6,14,28],'actual_disconnect_ticks':[8,16,30],'classes_for_witness':{'2':'warm','10':'hot','24':'cold'},'source_pins':{str(x):pin(x)for x in (B/'startup.py',B/'prepare.py')},'artifact_hashes':{x.name:pin(x)for x in out.iterdir()},'official_parameter_claim':False,'large_builder_integrated':False};(out/'READY.json').write_text(json.dumps(ready,indent=2));print(ready)
if __name__=='__main__':main()
