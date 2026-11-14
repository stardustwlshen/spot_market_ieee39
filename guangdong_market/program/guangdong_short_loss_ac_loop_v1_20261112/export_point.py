from pathlib import Path
from datetime import datetime,timedelta
import sys,json,gzip,copy,math,hashlib
sys.path.insert(0,str(Path(__file__).resolve().parent.parent/'guangdong_short_first_ac_v1_20261112'))
from adapter_diagnostic import map_candidate,digest
import argparse
ap=argparse.ArgumentParser();ap.add_argument('--out',required=True);ap.add_argument('--response',required=True);ap.add_argument('--review',required=True);ap.add_argument('--loss',type=float,required=True);args=ap.parse_args()
R=Path(__file__).resolve().parent.parent;B=Path(args.out);ADAPTER=R/'guangdong_short_first_ac_v1_20261112/adapter_diagnostic.py';S=R/'guangdong_rt_short_physical_v1_20261112';P=S/'pure39_v2';A=S/'actual39_v1'
sys.path.insert(0,str(R/'guangdong_rt_recommit_program_v1_20261112/vendor/guangdong_after_reset_r23_compressed_20261112/repo/src'))
from smm.gd_october_job_codec import loads
read=lambda p:json.loads(Path(p).read_text());sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
def write(name,x):
 with (B/name).open('x')as f:json.dump(x,f,indent=2,allow_nan=False)
source=read(A/'SOURCE.json')
for p,h in source['artifact_pins'].items():assert sha(p)==h
assert read(args.review)['KKT']['passed']
case=loads((P/'derived_case.json').read_bytes());e=case['energy_inputs'];accepted=read(A/'ACCEPTED_PAIR.json');assert accepted['SCED_audit']['KKT']['passed'] and accepted['status']=='accepted_research_pair'
v={k.replace("'",''):x for k,x in zip(read(P/'names.json')['variables'],json.loads(gzip.decompress(Path(args.response).read_bytes()))['primal'])}
benchmark_path=R/'guangdong_r30_ac_security_20261112/IEEE39_RESEARCH_INPUT.json';ac=read(benchmark_path);bg={g['bus']:g for g in ac['generators']};bb={b['id']:b for b in ac['buses']};start=datetime.fromisoformat(accepted['domain_start']);end=start+timedelta(minutes=5);agc=read(P/'agc.json');boundary=read(P/'BOUNDARY.json');t=0
controls={};resources=[];plan={};gens=[];capacity={};loads={};fixed=[]
def prow(p):return {'p_mean_mw':p,'p_endpoint_mw':p,'interval_start_seconds':start.timestamp(),'interval_end_seconds':end.timestamp()}
for b in ac['buses']:
 node=str(int(b['id'])+1);p=e['load_mw'][node,t];assert b['pd_mw']!=0 or p==0
 loads[b['id']]={'p_mw':p,'q_mvar':p*b['qd_mvar']/b['pd_mw']if b['pd_mw']else 0.}
 fp=e['physical_tie_mw'][node,t]
 if fp:fixed.append({'id':'fixed:'+node,'bus':b['id'],'p_mw':fp,'q_mvar':0.})
for u,z in e['thermal'].items():
 assert v[f'stable[{u},0]']>.5 and v[f'online[{u},0]']>.5
 assert all(abs(v[f'{stem}[{u},{t}]'])<1e-8 for stem in ('start','stop')for t in range(48))
 bus=str(int(z['bus'])-1);g=copy.deepcopy(bg[bus]);g.update(id=u,pg_mw=v[f'power[{u},0]'],qg_mvar=0.,pmin_mw=z['min_mw'][0],pmax_mw=z['max_mw'][0]);gens.append(g)
 up=agc[u]['up_mw']['0'];down=agc[u]['down_mw']['0'];r={'id':u,'bus':bus,'kind':'thermal','online':True,'phase':'stable','agc_up_mw':up,'agc_down_mw':down,'ramp_up_mw_per_h':z['ramp_up_mw_per_h'][0],'ramp_down_mw_per_h':z['ramp_down_mw_per_h'][0],'previous_endpoint_mw':boundary['thermal'][u]['end_power_mw'],'endpoint_semantics':'explicit_stable_piecewise_constant','reserve_eligible':case['reserve']['contingency_eligible'][u],'positive_cap_mw':g['pmax_mw']-g['pmin_mw']-up-down,'accident_cap_mw':z['ramp_up_mw_per_h'][0]/6}
 resources.append(r);plan[u]=prow(g['pg_mw']);controls[u]={'mode':'VG','vg_pu':g['vg_pu']}
 capacity[u]={'raw_pmax_mw':g['pmax_mw'],'raw_pmin_mw':g['pmin_mw'],'scheduled_p_mw':g['pg_mw'],'agc_up_reserved_mw':up,'agc_down_reserved_mw':down,'positive_cap_mw':r['positive_cap_mw'],'accident_cap_mw':r['accident_cap_mw'],'ramp_up_mw_h':r['ramp_up_mw_per_h'],'ramp_down_mw_h':r['ramp_down_mw_per_h']}
# Read static device inventory ONLY, no 17h controller or solved Q/V is read.
invpath=R/'guangdong_agc_ac_controls_v2_20261112/INVERTER_INVENTORY.json'
inv=read(invpath)
# Physical device identification is original source map. Pn is not used to manufacture reactive capability.
vre_map={'W1':('30','wind'),'W2':('37','wind'),'PV1':('33','pv'),'PV2':('4','pv'),'PV3':('8','pv')}
assert all(abs(v[f'renewable_power[{bus},0]'])<1e-8 for bus in e['buses']if bus not in {a for a,b in vre_map.values()})
for u,(node,kind)in vre_map.items():
 p=v[f'renewable_power[{node},0]'];resources.append({'id':u,'bus':str(int(node)-1),'kind':kind,'online':True,'reserve_eligible':False});plan[u]=prow(p);controls[u]={'mode':'PQ','q_mvar':0.}
for u,z in case['batteries'].items():
 ch=v[f'battery.charge[{u},0]'];dis=v[f'battery.discharge[{u},0]'];eta=math.sqrt(z['roundtrip_efficiency']);r={'id':u,'bus':str(int(z['bus'])-1),'kind':'battery','online':z['availability'][0],'reserve_eligible':False,'soc':{'previous_end_at':start.isoformat(),'start_mwh':z['initial_energy_mwh'],'previous_end_mwh':boundary['storage'][u]['initial_energy_mwh'],'end_mwh':v[f'battery.energy[{u},0]'],'min_mwh':z['energy_min_mwh'],'max_mwh':z['energy_max_mwh'],'eta_charge':eta,'eta_discharge':eta,'charge_max_mw':z['charge_max_mw'],'discharge_max_mw':z['discharge_max_mw']}}
 resources.append(r);plan[u]=dict(prow(dis-ch),charge_mw=ch,discharge_mw=dis);controls[u]={'mode':'PQ','q_mvar':0.}
ac['generators']=gens;ac['provenance']='Same original IEEE39 benchmark AC; original VG including known GT1.0636 conflict; physical gross Q/P proportional research proxy, VRE/BESS Q0; new accepted midnight short SCED mean point; not telemetry';ac['operating_point_kind']='research_snapshot'
d={'schema':'gd.explicit_sced_ac_candidate.v1','start':start.isoformat(),'end':end.isoformat(),'information_cutoff':case['contract']['cutoff'],'data_available_at':case['contract']['cutoff'],'power_semantics':'interval_mean_research_snapshot','physical_load_excludes_loss_proxy':True,'network':ac,'resources':resources,'plan':plan,'controls':controls,'gross_load':loads,'fixed_injections':fixed,'slack_resource':'C1','reserve_requirements':{'positive_mw':400.,'accident_mw':400.},'loss_proxy_mw':args.loss,'allow_original_VG_diagnostic':True,'capability':{'mode':'original_Q0','qualification_changed':False,'source_statement':'original inverter Q0, no new design invoked'}}
paths=[A/'ACCEPTED_PAIR.json',Path(args.response),Path(args.review),P/'derived_case.json',P/'names.json',P/'BOUNDARY.json',P/'agc.json',benchmark_path,invpath,Path(__file__),ADAPTER]
a={'payload_sha256':digest(d),'SCED_accepted':True,'full_domain_constraints_checked':True,'sources':[{'path':str(p.relative_to(R)),'sha256':sha(p)}for p in paths]}
m=map_candidate(d,a,R);m['thermal_capacity']=capacity
write('PAYLOAD.json',d);write('ACCEPTANCE_BINDING.json',a);write('MAPPING.json',m)
conflicts=[{'id':g['id'],'VG':g['vg_pu'],'Vmin':bb[g['bus']]['vmin_pu'],'Vmax':bb[g['bus']]['vmax_pu']}for g in gens if not bb[g['bus']]['vmin_pu']<=g['vg_pu']<=bb[g['bus']]['vmax_pu']]
write('PRECHECK.json',{'original_VG_conflicts_preserved':conflicts,'first_three_times':[(start+timedelta(minutes=5*i)).isoformat()for i in range(3)],'first_three_ticks':[0,1,2],'all48_thermal_stable_no_start_stop':True,'zero_loss_SCED':args.loss==0,'Q_source':'original benchmark gross Q/P proxy; no measured Q','no_future_17h_Q_VG_loaded':True,'original_Q0':True,'new_capability_not_instantiated':True,'precheck':m['precheck']})
print(m['precheck'],conflicts)
