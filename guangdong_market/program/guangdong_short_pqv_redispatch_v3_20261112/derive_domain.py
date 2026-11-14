from pathlib import Path
import json,gzip,math,hashlib,copy
B=Path(__file__).resolve().parent;R=B.parent;C=R/'guangdong_short_loss_ac_loop_v1_20261112/round_1'
read=lambda p:json.loads(Path(p).read_text());sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
q=json.loads(gzip.decompress((C/'SCED/request.json.gz').read_bytes()));res=json.loads(gzip.decompress((C/'SCED/response.json.gz').read_bytes()));x=res['primal'];n=read(R/'guangdong_rt_short_physical_v1_20261112/pure39_v2/names.json');vi={k.replace("'",''):i for i,k in enumerate(n['variables'])};p=read(C/'PAYLOAD.json');thermal={};batteries={}
for r in p['resources']:
 u=r['id']
 if r['kind']=='thermal':
  g=next(g for g in p['network']['generators']if g['id']==u);prev=r['previous_endpoint_mw'];nxt=x[vi[f'power[{u},1]']];up=r['ramp_up_mw_per_h']/12;down=r['ramp_down_mw_per_h']/12
  terms={'raw_with_AGC':[g['pmin_mw']+r['agc_down_mw'],g['pmax_mw']-r['agc_up_mw']],'previous_endpoint_ramp':[prev-down,prev+up],'next_endpoint_ramp':[nxt-up,nxt+down]};lo=max(v[0]for v in terms.values());hi=min(v[1]for v in terms.values());assert lo<=hi
  thermal[u]={'P_min_MW':lo,'P_max_MW':hi,'source_bounds':terms,'original_P_MW':x[vi[f'power[{u},0]']]}
 if u not in ('ES4','ES5'):continue
 s=r['soc'];j=vi[f'battery.discharge[{u},0]'];p0=x[j];assert p0>1e-8 and x[vi[f'battery.charge[{u},0]']]==0
 direction={j:1.}
 for t in range(48):direction[vi[f'battery.energy[{u},{t}]']]=-1/(12*s['eta_discharge'])
 low,high=-math.inf,math.inf;restrictions=[]
 def bound(name,a0,coef,lb,ub):
  global low,high
  if abs(coef)<1e-12:
   assert (lb is None or a0>=lb-1e-6)and(ub is None or a0<=ub+1e-6),(name,a0,lb,ub);return
  lo,hi=-math.inf,math.inf
  if lb is not None:
   if coef>0:lo=(lb-a0)/coef
   else:hi=(lb-a0)/coef
  if ub is not None:
   if coef>0:hi=(ub-a0)/coef
   else:lo=(ub-a0)/coef
  low=max(low,lo);high=min(high,hi);restrictions.append({'name':name,'delta_min':lo if math.isfinite(lo)else None,'delta_max':hi if math.isfinite(hi)else None})
 for k,c in direction.items():bound(n['variables'][k],x[k],c,q['variables'][k]['lb'],q['variables'][k]['ub'])
 for name,row in zip(n['constraints'],q['rows']):
  if not name.startswith('battery.')or name.startswith('battery.c_bid_power'):continue
  a0=math.fsum(a*x[k]for k,a in row['terms']);coef=math.fsum(a*direction.get(k,0)for k,a in row['terms']);bound(name,a0,coef,row['lb'],row['ub'])
 assert low<=high and low<=1e-6 and high>=-1e-6
 # Exact battery block validation at interval extrema and midpoint, redistributing
 # first-point bid segments inside existing widths. No new solver or future P changes.
 checks=[]
 for delta in [low,high,(low+high)/2]:
  y=x.copy()
  for k,c in direction.items():y[k]+=c*delta
  for name,row in zip(n['constraints'],q['rows']):
   if name.startswith('battery.c_bid_power')and any(k==j for k,a in row['terms']):
    segment=[k for k,a in row['terms']if k!=j];remaining=y[j]
    for k in segment:
     lower=q['variables'][k]['lb']or 0.;upper=q['variables'][k]['ub']
     if upper is None:
      gate=[rr for nn,rr in zip(n['constraints'],q['rows'])if nn.startswith('battery.c_segment')and any(jj==k for jj,aa in rr['terms'])];assert len(gate)==1
      rr=gate[0];coef=dict(rr['terms'])[k];assert coef>0 and rr['ub']is not None
      upper=(rr['ub']-math.fsum(aa*y[jj]for jj,aa in rr['terms']if jj!=k))/coef
     y[k]=min(upper,max(lower,remaining));remaining-=y[k]
    assert abs(remaining)<1e-6
  worst=0.
  for name,row in zip(n['constraints'],q['rows']):
   if name.startswith('battery.'):
    lhs=math.fsum(a*y[k]for k,a in row['terms']);worst=max(worst,0 if row['lb']is None else row['lb']-lhs,0 if row['ub']is None else lhs-row['ub'])
  assert worst<1e-6;checks.append({'P_MW':p0+delta,'all_battery_rows_max_violation':worst})
 batteries[u]={'P_min_MW':p0+low,'P_max_MW':p0+high,'original_P_MW':p0,'discharge_only_integer_mode_unchanged':True,'other47_charge_discharge_fixed':True,'all48_energy_shift_per_deltaP':-1/(12*s['eta_discharge']),'restrictions':restrictions,'endpoint_midpoint_block_checks':checks}
report={'thermal':thermal,'battery':batteries,'all_other_P_fixed':True,'all_integers_fixed':True,'no_epsilon_lower_bound_added':True,'full48_storage_SOC_terminal_cycle_ramp_projected':True,'qualification_unchanged':True,'not_complete_joint_network_feasible_domain':True,'coupled_AC_and_reserve_constraints_still_required':True,'new_LP_calls':0,'source_pins':{str(t.relative_to(R)):sha(t)for t in[C/'SCED/request.json.gz',C/'SCED/response.json.gz',C/'PAYLOAD.json',Path(__file__)]}}
(B/'LOCAL_P_DOMAIN.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps({'thermal':thermal,'battery':{k:{a:v[a]for a in ['P_min_MW','P_max_MW','original_P_MW']}for k,v in batteries.items()}},indent=2))
