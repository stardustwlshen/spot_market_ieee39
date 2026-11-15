"""Audited serialization-only power/SOC normalization. Native witness stays immutable."""
from pathlib import Path
from copy import deepcopy
from datetime import timedelta
import math,sys
B=Path(__file__).resolve().parent;R=B.parent
sys.path.insert(0,str(R/'guangdong_event_host_candidate_v1_20261113'))
from v6_host import installed,read,write,sha,e
TOL=1e-10

def replay(case,values,canonical_energy):
 storage={};proofs={};power=[]
 begin=e.stamp(case['contract']['domain_start']);delivery=begin+timedelta(minutes=15)
 for u,b in case.get('batteries',{}).items():
  E=b['initial_energy_mwh'];eta=math.sqrt(b['roundtrip_efficiency']);mode=b['initial_mode'];age=b['initial_mode_age_h'];ledger=deepcopy(b['cycle_budget']);terms=[];cells=[]
  for i in range(1):
   ch=values[f'battery.charge[{u},{i}]'];dis=values[f'battery.discharge[{u},{i}]'];cm=values[f'battery.charge_mode[{u},{i}]'];dm=values[f'battery.discharge_mode[{u},{i}]'];native=values[f'battery.energy[{u},{i}]']
   e.need(all(math.isfinite(x)for x in (ch,dis,cm,dm,native,E)),'nonfinite storage witness')
   e.need(abs(cm-round(cm))<=1e-6 and abs(dm-round(dm))<=1e-6 and round(cm)in (0,1)and round(dm)in (0,1)and round(cm)+round(dm)<=1,'invalid storage modes')
   new='charge'if cm>.5 else'discharge'if dm>.5 else'idle';age=age+1/4 if new==mode else 1/4;mode=new;term=(eta*ch-dis/eta)/4;terms.append(term);E+=term;fs=math.fsum([b['initial_energy_mwh'],*terms]);e.need(abs(E-native)<=1e-7 and abs(fs-native)<=1e-7,'one-cell SOC replay failed')
   date=(begin+timedelta(minutes=15*i)).date().isoformat();ledger[date]['used_internal_mwh']+=(eta*ch+dis/eta)/4
   cells.append(dict(tick=i,charge_mw=ch,discharge_mw=dis,mode=new,native_energy_mwh=native,sequential_energy_mwh=E,fsum_energy_mwh=fs))
  oldhour=(delivery-timedelta(minutes=15)).replace(minute=0,second=0,microsecond=0);hour=delivery.replace(minute=0,second=0,microsecond=0);keys=[k for k in values if k.startswith('battery.hour_direction['+u+',')and oldhour.isoformat()in k];e.need(len(keys)==1,'hour direction source')
  raw=dis-ch;storage[u]=dict(initial_energy_mwh=E,initial_power_mw=raw,initial_mode=mode,initial_mode_age_h=age,cycle_budget=ledger,initial_hour_direction=(1 if values[keys[0]]>.5 else-1)if oldhour==hour else 0)
  proofs[u]=dict(cells=cells,initial_energy_mwh=b['initial_energy_mwh'],raw_replayed=E,native_terminal=native,fsum_replayed=fs,lower_bound=b['energy_min_mwh'],upper_bound=b['energy_max_mwh'],mode=mode,raw_power=raw)
  wrong=(raw!=0 if mode=='idle'else raw>0 if mode=='charge'else raw<0)
  if wrong:
   e.need(abs(raw)<=TOL,'material mode/power contradiction')
   power.append(dict(unit=u,field='initial_power_mw',raw=raw,canonical=0.,delta_mw=-raw,tolerance_mw=TOL,reason='accepted native zero-bound roundoff; no SOC or cycle ledger change'));storage[u]['initial_power_mw']=0.
 # Reuse the frozen, source-verified canonicalizer including its fsum gate.
 energy=canonical_energy(storage,case,values)
 changed={r['unit']for r in power+energy}
 for u in changed:
  p=proofs[u];s=storage[u];e.need(abs(p['fsum_replayed']-p['native_terminal'])<=TOL,'normalization native/fsum mismatch')
  e.need(abs(p['fsum_replayed']-s['initial_energy_mwh'])<=TOL and abs(s['initial_energy_mwh']-p['raw_replayed'])<=TOL,'normalization material SOC delta')
  e.need(p['lower_bound']<=s['initial_energy_mwh']<=p['upper_bound'],'canonical SOC outside bounds')
  power_value=s['initial_power_mw'];m=s['initial_mode'];e.need(power_value==0 if m=='idle'else power_value<=0 if m=='charge'else power_value>=0,'canonical mode/sign mismatch')
 return storage,power,energy,proofs

def verify_bundle_storage(bundle):
 p=installed();case=e.unpack(bundle['case']);v=dict(zip(bundle['names']['variables'],bundle['SCED_response']['primal']))
 storage,power,energy,proofs=replay(case,v,p.engine.canonical_energy_boundaries)
 original,_,_=e.storage_carry(bundle);e.need(storage==original,'independent/event replay storage differs')
 receipt=dict(schema='gd.event.boundary_normalization.v1',SCED_response_digest=e.digest(bundle['SCED_response']),bundle_sha256=e.digest(bundle),power=power,energy=energy,three_cell_proofs=proofs,raw_witness_changed=False,cycle_ledger_changed=False,tolerance=1e-10,source_pins={str(Path(__file__).resolve()):sha(__file__),str(Path(p.engine.__file__).resolve()):sha(p.engine.__file__)},formal=False,safety_release=False,native_calls=0)
 receipt['receipt_sha256']=e.digest(receipt);return storage,receipt
