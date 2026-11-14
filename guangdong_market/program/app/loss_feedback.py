"""Pure matrix callback. No host registration, publication, solver or AC calls."""
from copy import deepcopy
from datetime import datetime,timedelta
import hashlib,json,math

def digest(z):return hashlib.sha256(json.dumps(z,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
def need(ok,msg):
 if not ok:raise ValueError(msg)
def apply_loss_rows(template,names,node_loss,*,tick,positive_row,negative_row):
 """Math only, not authority. Explicit reserve row identities required from compiler."""
 q=deepcopy(template);ci={s:i for i,s in enumerate(names['constraints'])};vi={s.replace("'",''):i for i,s in enumerate(names['variables'])};changes=[]
 need(len(ci)==len(names['constraints']),'duplicate rows')
 for node,loss in node_loss.items():
  need(math.isfinite(loss) and loss>=-1e-8,'negative/nonfinite loss')
  name=f"c_balance['{node}',{tick}]";i=ci[name];r=q['rows'][i];need(r['lb']==r['ub']and r['lb']is not None,'nodal equality required')
  orientation=dict(r['terms'])[vi[f'renewable_power[{node},{tick}]']];need(orientation in(-1.,1.),'unknown node orientation')
  for side in ('lb','ub'):r[side]+=orientation*loss
  changes.append({'name':name,'delta':orientation*loss})
 total=math.fsum(node_loss.values())
 for name,side,other in [(positive_row,'lb','ub'),(negative_row,'ub','lb')]:
  row=q['rows'][ci[name]];need(row[side]is not None and row[other]is None,'reserve inequality orientation unsupported')
  # Compiler contract: variable injections have positive coefficients on both rows.
  need(all(dict(row['terms']).get(vi[f'renewable_power[{node},{tick}]'])==1 for node in node_loss),'reserve injection structure changed')
  row[side]+=total;changes.append({'name':name,'side':side,'delta':total})
 need(q['variables']==template['variables']and q['objective']==template['objective'],'columns/objective drift')
 return q,changes

def build_unissued_feedback(template,names,mapping,ac,case,agc,context,expected_identity,*,positive_row,negative_row):
 """Call only from trusted before-handoff hook. context itself grants no authority.
 Offline tests cannot register this return as a live candidate.
 """
 for key in ('branch_id','seed_sha256','window','point','SCED_request_sha256','SCED_response_sha256'):
  need(context[key]==expected_identity[key],'identity mismatch: '+key)
 need(context['seed_sha256']=='c10e5cbfbe0f39f028d10cb2b2d30003e803033a3d883f4c753339bc9c1cdbe9','this adapter validation currently scoped to c10e case family')
 need(context['point']==mapping['start'],'point mismatch')
 point=datetime.fromisoformat(mapping['start']);end=datetime.fromisoformat(mapping['end']);frontier=datetime.fromisoformat(context['issued_end_at'])
 need(point.utcoffset()==timedelta(hours=8)and end-point==timedelta(minutes=5),'clock invalid')
 need(point>=frontier,'already issued prefix: same-point repair forbidden')
 need(context['stage']=='accepted_pair_before_handoff','must be before handoff')
 need(digest(template)==context['template_digest']and digest(names)==context['names_digest'],'model identity mismatch')
 need(ac['ac_converged']and ac['input_sha256']==digest(mapping['ac_input']),'AC/input source mismatch')
 tick=context['local_tick'];need(tick>=0,'invalid tick')
 need(all(set(case['reserve'][k].values())=={400}for k in ('positive_mw','contingency_mw')),'400/400 changed')
 need(sum(r['up_mw'][str(tick)]for r in agc.values())==sum(r['down_mw'][str(tick)]for r in agc.values())==50,'AGC50 changed')
 domain=datetime.fromisoformat(case['contract']['domain_start']);need(domain+timedelta(minutes=5*tick)==point,'case point mismatch')
 # Explicit ID map, never assume bus-number offsets in general interface.
 busmap=context['AC_to_model_bus'];need(set(busmap)=={b['id']for b in mapping['ac_input']['buses']}and set(busmap.values())==set(case['energy_inputs']['buses']),'bus coverage mismatch')
 branches={b['id']:b for b in mapping['ac_input']['branches']};need({r['id']for r in ac['branches']}==set(branches),'branch inventory mismatch')
 losses={node:0. for node in busmap.values()}
 for row in ac['branches']:
  loss=row['from_p_mw']+row['to_p_mw'];need(loss>=-1e-8,'unexpected negative real branch loss')
  for side in ('from_bus','to_bus'):losses[busmap[branches[row['id']][side]]]+=loss/2
 need(all(b['gs_mw']==0 for b in mapping['ac_input']['buses']),'nonzero real shunt loss requires explicit allocation')
 q,changed=apply_loss_rows(template,names,losses,tick=tick,positive_row=positive_row,negative_row=negative_row)
 return q,{'node_loss_MW':losses,'changed_rows':changed,'basis':'same-point numerical AC proxy, not measured load','physical_AC_load_unchanged':True,'source_identity':deepcopy(expected_identity),'live_registered':False,'publication_allowed':False,'safety_release':False,'native_calls':0}
