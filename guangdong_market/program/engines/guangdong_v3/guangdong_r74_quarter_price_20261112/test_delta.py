from pathlib import Path
import json
import pytest
B=Path(__file__).resolve().parent;R=B.parent
read=lambda p:json.loads(p.read_bytes())
@pytest.mark.parametrize('w',[0,25,27,32,48,95])
def test_exact_projection_and_ramp_scope(w):
 d=B/f'batch_six_v1/W{w}';new=read(d/'template.json');nn=read(d/'names.json');t=read(d/'transformation.json');inp=read(d/'input.json');old=R/'guangdong_r69_tail_source_price_20261112'/('batch_W0_v1/W0'if w==0 else f'batch_W1_95_v1/W{w}');q=read(old/'template.json');n=read(old/'names.json');mp={int(j):k for j,k in t['old_to_new_columns'].items()};removed=set(t['removed_rows']);changed={z['row_index']:z['new']for z in t['changed_throughput_rows']}
 def mapped(r):return dict(r,terms=[[mp[j],v]for j,v in r['terms']])
 rows=[mapped(changed.get(j,r))for j,r in enumerate(q['rows'])if j not in removed]
 assert new['rows'][:len(rows)]==rows and new['objective']==mapped(q['objective']) and new['options']==q['options']
 assert all(new['variables'][k]==q['variables'][j]for j,k in mp.items())
 assert len(q['variables'])-len(new['variables'])==480 and len(removed)==720 and len(new['rows'])-len(rows)==160
 assert not any(s.split('[')[0]in ('lift','vre_power','vre_curtailment')for s in nn['variables'])
 for uid,u in inp['resources'].items():
  actual=inp['projection']['state']['batteries'if u['kind']=='battery'else'thermal'][uid]['power_mw']
  for sign,key,label in [(1,'ramp_up_mw_h','up'),(-1,'ramp_down_mw_h','down')]:
   row=new['rows'][nn['constraints'].index(f'R74_quarter_ramp[{uid},0,{label}]')]
   assert row['ub']==.25*u['rows'][0][key]+sign*actual
 if w==95:
  assert inp['window']['midnight_local_energy_index']==3
  for uid,target in inp['midnight_targets'].items():
   row=new['rows'][nn['constraints'].index(f'midnight[{uid}]')];assert row['lb']==row['ub']==target
