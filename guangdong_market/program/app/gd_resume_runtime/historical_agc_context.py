"""Read-only effective AGC case from an accepted, source-pinned historical phase.
This restores consumer data, never a LivePhase, solver acceptance or new award.
"""
from pathlib import Path
from copy import deepcopy
from datetime import timedelta
import json,gzip
from content_hash import sha

def need(ok,message):
 if not ok:raise ValueError(message)
def read(p):
 p=Path(p);return json.loads(gzip.decompress(p.read_bytes())if p.suffix=='.gz'else p.read_bytes())

def restore(folder,evidence,source_pins,*,decode):
 from gd_post_scuc_runtime.phase_runtime import digest,fixed,stamp
 folder=Path(folder).resolve();pins={str(Path(p).resolve()):h for p,h in source_pins.items()};guards={}
 def bound(p):
  p=Path(p).resolve();h=pins.get(str(p));need(h is not None and sha(p)==h,'historical effective AGC source unbound or changed: '+str(p));guards[str(p)]=h;return p
 pair=read(bound(folder/'actual/ACCEPTED_PAIR.json'));proof=pair['post_SCUC_phase'];need(proof==evidence['phase_proof'],'historical effective AGC phase mismatch')
 lineage=folder/'HISTORICAL_PAIR_LINEAGE.json'
 phase_dir=folder/'post_scuc_phase'
 if lineage.is_file():
  z=read(bound(lineage));need(z['schema']=='gd.explicit_owned_historical_pair.v1'and z['new_phase_reconstructed']is True and z['new_native_calls']==0,'unsupported historical phase lineage');need(sha(z['historical_pair'])==z['historical_pair_sha256'],'historical original pair changed');phase_dir=folder/'new_owned_phase'
 phase=phase_dir/'full_build_2'
 template=read(bound(phase/'template.json'));names=read(bound(phase/'names.json'))
 c=decode(bound(phase/'derived_case.json').read_bytes());awards=read(bound(phase/'agc.json'));carry=read(bound(phase/'CARRY.json'))
 need('agc_awards'not in c and 'boundary_states'not in c,'unexpected serialized builder context shape')
 c['agc_awards']={g:{**a,'up_mw':{int(t):v for t,v in a['up_mw'].items()},'down_mw':{int(t):v for t,v in a['down_mw'].items()}}for g,a in awards.items()}
 c['boundary_states']=deepcopy(carry['thermal'])
 hour=evidence['hour'];export=read(bound(folder/f'H{hour}_RESEARCH_AGC_RECEIPT.json'));receipt=evidence['receipt'];declaration=export['declaration']
 need(export['receipt']==receipt and digest(receipt)==proof['receipt'],'historical effective receipt mismatch')
 need(receipt['published_at']==declaration['as_of']and receipt['hour_start']==declaration['hour_start'],'historical effective receipt clock mismatch')
 begin=stamp(c['contract']['domain_start']);start=stamp(receipt['hour_start']);end=stamp(receipt['hour_end'])
 ticks=[t for t in c['agc_awards'][next(iter(c['agc_awards']))]['up_mw']if start<=begin+timedelta(minutes=5*t)<end]
 need(len(ticks)==12,'historical selected hour is not complete')
 # Exact original PostSCUC.prepare metadata/value transformation, not inferred awards.
 for g,a in c['agc_awards'].items():
  a['available_at']=receipt['published_at'];a['source']='postSCUC_internal_research:'+digest(declaration)
  for t in ticks:
   for side in ('up_mw','down_mw'):a[side][t]=receipt['allocator']['awards_mw'].get(g,0)
 need(digest(c)==proof['case'],'restored historical effective case differs from accepted case digest')
 u=read(bound(folder/'actual/SCUC/response.json.gz'));eq=read(bound(folder/'actual/SCED/request.json.gz'))
 need(digest(u['primal'])==proof['integer_source'],'historical integer source differs')
 need(sha(folder/'actual/SCUC/response.json.gz')==pair['SCUC_response_sha256'],'historical SCUC raw differs')
 lp=fixed(template,u['primal'])
 need(digest(lp)==proof['model']and digest(names)==proof['names'],'historical effective model/names differ from phase proof')
 need(all(lp[k]==eq[k]for k in ('variables','rows','objective')),'historical effective model differs from accepted SCED request')
 return dict(effective_case=c,guards=guards,proof=dict(schema='gd.historical_effective_agc.restore.v1',case_digest=proof['case'],model_digest=proof['model'],names_digest=proof['names'],receipt_digest=proof['receipt'],local_selected_ticks=ticks,source_pins=guards,restores_consumer_data_only=True,new_live_phase=False,new_award=False,new_native_calls=0,new_KKT_replay=False))
