"""Serialized 8-quarter PRICE research projection, no Pyomo or native calls."""
from pathlib import Path
from fractions import Fraction as F
from copy import deepcopy
import json,hashlib,argparse
B=Path(__file__).resolve().parent;R=B.parent
read=lambda p:json.loads(Path(p).read_bytes());sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
def write(p,x):
 with p.open('x')as f:json.dump(x,f,sort_keys=True,separators=(',',':'),allow_nan=False)
def transform(q,n,inp):
 ix={s.replace("'",''):j for j,s in enumerate(n['variables'])};remove={j for j,s in enumerate(n['variables'])if s.split('[')[0]in ('lift','vre_power','vre_curtailment')};rrows=set();changed={};proof=[];new=[];newnames=[]
 for ri,row in enumerate(q['rows']):
  hit={j for j,c in row['terms']} & remove
  if not hit:continue
  name=n['constraints'][ri];terms=dict(row['terms']);families={n['variables'][j].split('[')[0] for j in terms}
  if name.startswith('daily_throughput['):
   assert families=={'lift'} and row['lb']is None
   uid=name.split('[',1)[1].split(',')[0].replace("'",'');mapped=[]
   for k in range(8):
    ids=[ix[f'lift[{uid},{j}]']for j in range(3*k,3*k+3)];cs=[F(terms.get(j,0))for j in ids];assert len(set(cs))==1
    pc=ix[f'power[{uid},{k}]'];means=[z for z in q['rows']if set(dict(z['terms']))==set(ids+[pc])];assert len(means)==1
    mt=dict(means[0]['terms']);coef=-cs[0]*F(mt[pc])/F(mt[ids[0]])
    if coef:mapped.append([pc,float(coef)])
   changed[ri]=dict(row,terms=mapped);proof.append(dict(row_index=ri,name=name,change='exact substitution using original serialized mean coefficients',old=row,new=changed[ri]));continue
  if families<= {'lift'}:
   assert name.startswith('rows[') and len(terms)<=2 and row['ub']is not None,'unexpected lift-only row'
   assert set(terms.values())<={-1.,1.};rrows.add(ri);continue
  if families=={'power','lift'}:
   assert row['lb']==row['ub']==0 and len(terms)==4;rrows.add(ri);continue
  if families=={'vre_power','vre_curtailment'}:
   assert name.startswith('vre_rows[')and row['lb']==row['ub']and len(terms)==2 and set(terms.values())=={1.};rrows.add(ri);continue
  if families=={'vre_quarter_power','vre_power'}:
   assert name.startswith('vre_rows[')and row['lb']==row['ub']==0 and len(terms)==4;rrows.add(ri);continue
  raise AssertionError(('unexpected removed variable use',name,families))
 for uid,u in inp['resources'].items():
  previous=inp['projection']['state']['batteries'if u['kind']=='battery'else'thermal'][uid]['power_mw']
  for k in range(8):
   cells=u['rows'][3*k:3*k+3]
   if u['kind']!='battery':assert all(z['phase']=='stable'for z in cells),'transition pricing mapping not invented'
   for sign,field in [(1.,'ramp_up_mw_h'),(-1.,'ramp_down_mw_h')]:
    rates={z[field]for z in cells};assert len(rates)==1;rate=rates.pop();rhs=rate*.25;terms=[[ix[f'power[{uid},{k}]'],sign]]
    if k:terms.append([ix[f'power[{uid},{k-1}]'],-sign])
    else:rhs+=sign*previous
    new.append(dict(lb=None,ub=rhs,terms=terms));newnames.append(f'R74_quarter_ramp[{uid},{k},{"up"if sign==1 else"down"}]')
 assert len(remove)==480 and len(rrows)==720 and len(new)==160
 kept=[j for j in range(len(q['variables']))if j not in remove];mapping={j:k for k,j in enumerate(kept)}
 def remap(row):
  out=deepcopy(row);assert not (set(dict(row['terms']))&remove);out['terms']=[[mapping[j],v]for j,v in row['terms']];return out
 out=deepcopy(q);out['variables']=[out['variables'][j]for j in kept];out['rows']=[remap(changed.get(i,row))for i,row in enumerate(q['rows'])if i not in rrows]+[remap(z)for z in new];out['objective']=remap(q['objective'])
 names=dict(variables=[n['variables'][j]for j in kept],constraints=[s for i,s in enumerate(n['constraints'])if i not in rrows]+newnames)
 tr=dict(removed_columns=sorted(remove),removed_rows=sorted(rrows),old_to_new_columns=mapping,changed_throughput_rows=proof,added_quarter_ramp_rows=newnames,actual_previous_power_unchanged=True,quarter_SOC_D96_unchanged=True,DA_band_delta_unchanged=.1,quarter_VRE_conservation_unchanged=True,quarter_variable_bounds_unchanged=True,objective_unchanged=True,RT_model_unchanged=True,official_mapping_certified=False)
 return out,names,tr

def prepare(windows,output):
 out=Path(output);out.mkdir(parents=True,exist_ok=False);items=[]
 for w in windows:
  base=R/'guangdong_r69_tail_source_price_20261112'/('batch_W0_v1/W0'if w==0 else f'batch_W1_95_v1/W{w}');m=read(base/'PREPARATION.json')
  for f,h in m['artifacts'].items():assert sha(base/f)==h
  q=read(base/'template.json');n=read(base/'names.json');inp=read(base/'input.json');new,names,tr=transform(q,n,inp)
  d=out/f'W{w}';d.mkdir();pins={str((base/f).resolve()):sha(base/f)for f in ('PREPARATION.json','template.json','names.json','input.json')}
  native=R/'guangdong_r69_actual_price_20261112'/('native_W0_v1'if w==0 else'native_W1_95_v1')/f'W{w}'
  pins[str((native/'response.json').resolve())]=sha(native/'response.json');tr.update(window=w,source_pins=pins,old_native_status=read(native/'response.json')['native_status'],research_only=True)
  for f,x in [('input.json',inp),('template.json',new),('names.json',names),('transformation.json',tr)]:write(d/f,x)
  meta=dict(schema='gd.research.r74.quarter_PRICE.preparation.v1',window=w,native_calls=0,source_pins={**m['source_pins'],**pins},source_code_pins={str(p.resolve()):sha(p)for p in B.glob('*.py')},artifacts={p.name:sha(p)for p in d.iterdir()},rows=len(new['rows']),columns=len(new['variables']),market_price=None,settlement_price=None)
  write(d/'PREPARATION.json',meta);items.append(dict(window=w,prepared=str(d.resolve()),preparation_sha256=sha(d/'PREPARATION.json')))
 write(out/'BATCH_PLAN.json',dict(schema='gd.research.r74.PRICE_only_C_batch.v1',items=items,native_calls=0,formal_publication_allowed=False));print('PLAN',sha(out/'BATCH_PLAN.json'))
if __name__=='__main__':
 ap=argparse.ArgumentParser();ap.add_argument('--windows',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();prepare([int(x)for x in a.windows.split(',')],a.output)
