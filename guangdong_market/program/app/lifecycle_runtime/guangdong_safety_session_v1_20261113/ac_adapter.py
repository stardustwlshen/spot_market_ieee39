"""Pure explicit SCED-to-AC control mapping. Never calls a solver or grants safety."""
from copy import deepcopy
from datetime import datetime,timedelta
from pathlib import Path
import hashlib,json,math

def digest(obj):return hashlib.sha256(json.dumps(obj,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
def need(ok,msg):
 if not ok:raise ValueError(msg)
def stamp(s):
 t=datetime.fromisoformat(s);need(t.utcoffset()==timedelta(hours=8),'UTC+8 required');return t
def unique(rows,key='id'):
 d={r[key]:r for r in rows};need(len(d)==len(rows),'duplicate resource/bus');return d
def close(a,b,label,tol=1e-6):need(abs(a-b)<=tol,label)

def validate_network(ac):
 need(ac.get('schema')=='r30.ac_snapshot.v1' and ac['base_mva']>0 and ac['frequency_hz']>0,'explicit AC schema/base required')
 buses=unique(ac['buses']);need(bool(buses),'empty buses')
 for b in buses.values():
  need(isinstance(b['id'],str) and b['base_kv']>0 and 0<b['vmin_pu']<=b['vmax_pu'],'invalid bus limits')
  need(b['vm_pu']>0,'invalid initial V')
 gens=unique(ac['generators'])
 for g in gens.values():
  need(g['bus'] in buses and g['pmin_mw']<=g['pmax_mw'] and g['qmin_mvar']<=g['qmax_mvar'],'invalid generator limits/bus')
 branches=unique(ac['branches']);need(bool(branches),'empty network')
 adj={k:set() for k in buses}
 for line in branches.values():
  a,b=line['from_bus'],line['to_bus'];need(a in buses and b in buses and a!=b,'invalid branch bus')
  need(line['rate_mva']>0 and line['r_pu']>=0 and abs(line['r_pu'])+abs(line['x_pu'])>0 and line['tap']>=0 and line['status'] in(0,1),'invalid branch data')
  if line['status']:adj[a].add(b);adj[b].add(a)
 seen=set();stack=[next(iter(buses))]
 while stack:
  u=stack.pop()
  if u in seen:continue
  seen.add(u);stack.extend(adj[u]-seen)
 need(seen==set(buses),'islanded network requires explicit separate adapter')

def map_candidate(payload,acceptance,source_root,*,reactive_source_root=None):
 """Acceptance is an explicit caller-supplied evidence binding, not a verifier of SCED KKT.
 Generic schema accepts stable thermal mean-P snapshots only. Nonstable phases fail closed.
 """
 need(acceptance['payload_sha256']==digest(payload),'payload/source identity mismatch')
 need(acceptance['SCED_accepted'] is True and acceptance['full_domain_constraints_checked'] is True,'accepted SCED evidence required')
 need(bool(acceptance['sources']),'source evidence required')
 for ref in acceptance['sources']:
  p=Path(source_root)/ref['path'];need(p.is_file() and hashlib.sha256(p.read_bytes()).hexdigest()==ref['sha256'],'source missing/changed: '+ref['path'])
 # JSON serialization above rejects NaN/Infinity throughout numerical input.
 d=deepcopy(payload);need(d['schema']=='gd.explicit_sced_ac_candidate.v1','schema')
 start,end,cut=map(stamp,(d['start'],d['end'],d['information_cutoff']))
 need(end-start==timedelta(minutes=5) and cut<=start,'invalid interval/cutoff')
 need(stamp(d['data_available_at'])<=cut,'noncausal operating data')
 need(d['power_semantics']=='interval_mean_research_snapshot','endpoint/mean ambiguity')
 need(d['physical_load_excludes_loss_proxy'] is True,'loss proxy must not be AC demand')
 need(d['capability']['mode'] in ('original_Q0','new_research_inverter'),'explicit capability mode')
 need(d['capability']['qualification_changed'] is False,'qualification cannot change')
 if d['capability']['mode']=='new_research_inverter':need(bool(d['capability']['source_statement']),'capability assumptions/source required')
 ac=d['network'];validate_network(ac);buses=unique(ac['buses']);gens=unique(ac['generators']);resources=unique(d['resources']);controls=d['controls'];plan=d['plan']
 need(set(plan)==set(resources)==set(controls),'missing/extra resource in plan/control')
 need(set(d['gross_load'])==set(buses),'load bus coverage mismatch')
 need(d['slack_resource'] in gens,'explicit slack required')
 thermal={k for k,r in resources.items() if r['kind']=='thermal'}
 need(thermal==set(gens),'thermal/network generator coverage mismatch')
 for b in buses.values():
  gross=d['gross_load'][b['id']];b['pd_mw']=gross['p_mw'];b['qd_mvar']=gross['q_mvar'];b['type']='PQ'
 for f in d['fixed_injections']:
  need(f['bus'] in buses,'fixed injection unknown bus');buses[f['bus']]['pd_mw']-=f['p_mw'];buses[f['bus']]['qd_mvar']-=f['q_mvar']
 positive=accident=0.;ledger=[]
 for uid,r in resources.items():
  need(r['bus'] in buses,'resource unknown bus');p=plan[uid]['p_mean_mw'];c=controls[uid];bus=buses[r['bus']]
  need(r['kind'] in ('thermal','wind','pv','battery'),'unsupported resource kind')
  need(r['online'] or abs(p)<=1e-8,'offline nonzero P')
  close(plan[uid]['interval_start_seconds'],start.timestamp(),'plan time mismatch')
  close(plan[uid]['interval_end_seconds'],end.timestamp(),'plan time mismatch')
  if r['kind']=='thermal':
   need(r['online'] and r['phase']=='stable','nonstable/offline thermal needs explicit phase-to-AC compiler')
   g=gens[uid];need(g['bus']==r['bus'],'thermal bus mismatch')
   need(c['mode']=='VG','thermal VG command')
   if not bus['vmin_pu']<=c['vg_pu']<=bus['vmax_pu']:
    need(d.get('allow_original_VG_diagnostic') is True and d['capability']['mode']=='original_Q0' and c['vg_pu']==g['vg_pu'],'thermal VG command out of bounds; only unchanged original diagnostic allowed')
   g['pg_mw']=p;g['vg_pu']=c['vg_pu']
   up,down=r['agc_up_mw'],r['agc_down_mw'];need(up>=0 and down>=0,'negative AGC')
   need(g['pmin_mw']+down-1e-6<=p<=g['pmax_mw']-up+1e-6,'thermal P/AGC headroom')
   need(r['ramp_up_mw_per_h']>=0 and r['ramp_down_mw_per_h']>=0,'ramp bounds')
   delta=plan[uid]['p_endpoint_mw']-r['previous_endpoint_mw']
   need(-r['ramp_down_mw_per_h']/12-1e-6<=delta<=r['ramp_up_mw_per_h']/12+1e-6,'endpoint ramp')
   need(r['endpoint_semantics']=='explicit_stable_piecewise_constant','stable mean/end relation not declared')
   close(p,plan[uid]['p_endpoint_mw'],'mean is not endpoint under declared constant phase')
   bus['type']='SLACK' if uid==d['slack_resource'] else 'PV'
   head=max(0.,g['pmax_mw']-up-p)
   if r['reserve_eligible']:
    positive+=min(head,r['positive_cap_mw']);accident+=min(head,r['accident_cap_mw'])
  else:
   need(c['mode']=='PQ','inverter must be fixed PQ');q=c['q_mvar'];need(not r['reserve_eligible'],'nonthermal qualification unsupported, not silently counted')
   if d['capability']['mode']=='original_Q0':close(q,0.,'original Q0 scenario cannot use new Q')
   else:
    cap=d['capability']['devices'][uid];need(cap['bus']==r['bus'],'capability bus mismatch')
    need(cap['Pn_MW']>0 and cap['S_MVA']>0 and cap['Qcap_Mvar']>=0,'invalid capability')
    need(abs(p)<=cap['Pn_MW']+1e-6,'P exceeds installed nameplate')
    need(abs(q)<=cap['Qcap_Mvar']+1e-6,'Qcap')
    need(p*p+q*q<=min(1.,bus['vmin_pu']**2)*cap['S_MVA']**2+1e-6,'conservative low-voltage current/S bound')
   if not r['online'] or abs(p)<=1e-8:close(q,0.,'zero-P/offline Q disabled by contract')
   bus['pd_mw']-=p;bus['qd_mvar']-=q
   if r['kind']=='battery':
    s=r['soc'];ch,dis=plan[uid]['charge_mw'],plan[uid]['discharge_mw']
    need(ch>=0 and dis>=0 and min(ch,dis)<=1e-8,'simultaneous charge/discharge')
    need(ch<=s['charge_max_mw']+1e-6 and dis<=s['discharge_max_mw']+1e-6,'storage P limit')
    need(0<s['eta_charge']<=1 and 0<s['eta_discharge']<=1,'storage efficiency')
    close(p,dis-ch,'storage signed P')
    need(stamp(s['previous_end_at'])==start,'SOC predecessor time mismatch')
    close(s['start_mwh'],s['previous_end_mwh'],'SOC continuity/reset')
    close(s['end_mwh'],s['start_mwh']+(s['eta_charge']*ch-dis/s['eta_discharge'])/12,'SOC balance')
    need(all(s['min_mwh']-1e-6<=s[k]<=s['max_mwh']+1e-6 for k in ('start_mwh','end_mwh')),'SOC limit')
  ledger.append({'id':uid,'bus':r['bus'],'p_mw':p})
 if d['capability']['mode']=='new_research_inverter':need(set(d['capability']['devices'])==set(resources)-thermal,'capability resource coverage')
 need(d['reserve_requirements']=={'positive_mw':400.,'accident_mw':400.},'original dual400 required')
 need(positive>=400-1e-6 and accident>=400-1e-6,'dual400 insufficient')
 physical_net=math.fsum(g['pg_mw']for g in ac['generators'])-math.fsum(b['pd_mw']for b in ac['buses'])
 close(physical_net,d['loss_proxy_mw'],'plan energy balance/loss accounting')
 # No implicit conversion between external bus numbers and AC IDs.
 need(len({g['bus']for g in ac['generators']})==len(gens),'multiple thermal at bus requires allocation')
 need(sum(b['type']=='SLACK'for b in ac['buses'])==1,'exactly one slack')
 reactive=None
 if 'reactive_assets' in d:
  import importlib.util
  reactive_path=Path(__file__).resolve().with_name('reactive_assets.py')
  reactive_spec=importlib.util.spec_from_file_location('_source_bound_reactive_assets',reactive_path)
  reactive_module=importlib.util.module_from_spec(reactive_spec);reactive_spec.loader.exec_module(reactive_module)
  reactive=reactive_module.compile_reactive_assets(ac,d['reactive_assets'],source_root=source_root if reactive_source_root is None else reactive_source_root,start=d['start'],end=d['end'],information_cutoff=d['information_cutoff'])
  ac=reactive['ac_input'];validate_network(ac)
  close(math.fsum(g['pg_mw']for g in ac['generators'])-math.fsum(b['pd_mw']for b in ac['buses']),d['loss_proxy_mw'],'reactive device loss double-count/energy mismatch')
 result={'ac_input':ac,'source_payload_sha256':digest(payload),'source_evidence':deepcopy(acceptance),
         'start':d['start'],'end':d['end'],'ledger':ledger,'precheck':{'positive_mw':positive,'accident_mw':accident,'net_generation_minus_physical_load_mw':physical_net},
         'capability_mode':d['capability']['mode'],'AC_executed':False,'research_safety_passed':False,'formal_release':False,'security_release':False,'PRICE_pending':True,
         'limitations':['mean-P balanced snapshot only, not endpoint or dynamic proof','SCED acceptance and full-domain SOC/phase/cycle evidence remains caller responsibility','no N-1/AVC dynamics or new reactive optimization performed','slack actual P/current/voltage/line must be independently AC-checked']}

 if reactive is not None:
  result['reactive_assets']={k:v for k,v in reactive.items() if k!='ac_input'}
  result['reactive_normal_use_blocked']=bool(reactive['gates']['block_normal_price'])
 return result
