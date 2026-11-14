import itertools,unittest
import pyomo.environ as p
from component5 import StartupDTO,Curve,attach,classify
class Test(unittest.TestCase):
 def test_exhaustive_projection_same_explicit_tail(self):
  curve=Curve((25.,75.),(0.,50.),(50.,100.),10.,0,'synthetic');down=Curve((50.,),(100.,),(0.,),2.,0,'synthetic')
  d=StartupDTO('research','synthetic',5,'disconnect_to_start_curve_begin','test2/4',2,4,'off',3,'test',None,None,'research',{k:curve for k in ('hot','warm','cold')},down,classification_boundary_policy='hot_lt_H_warm_lt_W')
  m=p.ConcreteModel();b=attach(m,'u',d,6);count=0;original_tail_extra=0
  for bits in itertools.product((0,1),repeat=12):
   starts={t for t in range(6)if bits[t]};stops={t for t in range(6)if bits[6+t]};off=1;stable=0;age=3;kinds={};states=[];valid=True
   for t in range(7):
    if t:age=0 if t-1 in stops else age+1 if off else 0
    if t in starts:kinds[t]=classify(d,age)
    prev_off=off;prev_stable=stable;off+=int(t-1 in stops)-int(t in starts);stable+=int(t-2 in starts)-int(t in stops)
    active=sum(s<=t<s+2 for s in starts)+sum(s<=t<s+1 for s in stops)
    if off not in(0,1) or stable not in(0,1)or off+stable+active!=1 or (t in starts and not(prev_off or t-1 in stops))or(t in stops and not(prev_stable or t-2 in starts)):valid=False;break
    states.append((off,stable,age))
   if not valid:continue
   if any(t+2>6 for t in starts):original_tail_extra+=1;continue
   count+=1
   for t,(off,stable,age)in enumerate(states):
    b.off[t].set_value(off);b.stable[t].set_value(stable);b.offline_ticks[t].set_value(age)
    if t<6:
     b.start[t].set_value(int(t in starts));b.stop[t].set_value(int(t in stops))
     for k in b.K:b.kind[t,k].set_value(int(kinds.get(t)==k));b.notice[t,k].set_value(int(kinds.get(t)==k))
   for z in b.c.values():
    value=p.value(z.body);self.assertTrue(z.lower is None or value>=p.value(z.lower)-1e-9);self.assertTrue(z.upper is None or value<=p.value(z.upper)+1e-9)
   self.assertEqual(p.value(b.event_fee),10*len(starts)+2*len(stops))
  self.assertGreater(count,5);self.assertGreater(original_tail_extra,0);print('exhaustive schedules',4096,'original feasible with agreed tail',count,'excluded unfinished-tail counterexamples',original_tail_extra)
if __name__=='__main__':unittest.main()
