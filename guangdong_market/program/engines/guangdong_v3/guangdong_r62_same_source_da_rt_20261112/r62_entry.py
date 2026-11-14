"""Verify existing accepted new DA receipts, then form a physical-only release."""
from pathlib import Path
import sys,json,hashlib,math
from copy import deepcopy
B=Path(__file__).resolve().parent;R=B.parent
sys.path[:0]=[str(R/'guangdong_r53_same_source_da_chain_20261112'),str(R/'guangdong_r46_curtailable_full_day_20261112'),str(R/'guangdong_after_reset_r23_compressed_20261112/repo/src')]
from r46_entry import assemble_slices,require,digest,sha
read=lambda p:json.loads(Path(p).read_bytes())
def prepare_from_actual(*,price_prepared,price_review,physical_path,price_native_paths,window_count=96,start_index=0):
 p=Path(price_prepared);r=Path(price_review);root=R/'guangdong_r53_same_source_da_chain_20261112';chainpath=root/'DA_CHAIN_RESULT.json';chain=read(chainpath);pins={str(chainpath):sha(chainpath)}
 for stage in ('SCUC','SCED','PRICE'):
  path=Path(chain[stage+'_review_path']);require(sha(path)==chain[stage+'_review_sha256'],'accepted '+stage+' review changed');pins[str(path.resolve())]=sha(path)
 sr=read(chain['SCED_review_path']);pr=read(chain['PRICE_review_path']);require(sr['status']=='pass_new_R53_fixed_SCED' and pr['status']=='pass_new_eligible_VRE_PRICE','new actual accepted DA chain required')
 require(sha(physical_path)==sr['physical_solution_sha256'],'SCED physical binding')
 meta=read(p/'PREPARATION.json')
 for name,h in meta['artifact_hashes'].items():require(sha(p/name)==h,'new prepared artifact changed');pins[str((p/name).resolve())]=h
 for group in ('source_pins','source_code_pins'):
  for path,h in meta[group].items():require(sha(path)==h,'new source changed');pins[str(Path(path).resolve())]=h
 for role,z in pr['event_pins'].items():require(sha(price_native_paths[role])==z['sha256'],'actual PRICE event mismatch');pins[str(Path(price_native_paths[role]).resolve())]=z['sha256']
 wrapper=read(p/'input.json');bridge=read(p/'base_bridge.json');d=wrapper['base_DTO'];physical=read(physical_path);policy=wrapper['same_bid_policy']
 require(policy['noquote_energy_bid']['yuan_per_mwh']==-50 and policy['curtailment_penalty']['yuan_per_mwh']==1000,'whole-chain cost baseline')
 require(wrapper['source_receipt']['SCUC_accepted'] and wrapper['source_receipt']['same_bid_policy_sha256']==sr['same_bid_policy_sha256'],'same new SCUC/SCED policy')
 require(digest(d)==bridge['DTO_sha256'] and sha(p/'input.json')==pr['source_input_sha256'],'PRICE source identity')
 rawpath=r/'RAW_UNCLIPPED_LMPS98.json';raw=read(rawpath);require(sha(rawpath)==pr['raw_unclipped_LMP_sha256'],'raw PRICE binding');prices=raw['raw_unclipped_LMP'];require(len(prices)==39 and all(len(v)==98 for v in prices.values()),'all raw prices')
 rows=[]
 for q,g in enumerate(d['grid'][:96]):
  qty={uid:math.fsum(physical['physical_cells'][j]['thermal'][uid]['mean_mw'] if u['kind']!='battery' else physical['physical_cells'][j]['battery'][uid]['discharge_mw']-physical['physical_cells'][j]['battery'][uid]['charge_mw'] for j in range(3*q,3*q+3))/12 for uid,u in d['resources'].items()}
  rows.append(dict(index=q,start=g['start'],end=g['end'],quantity_mwh=qty))
 release=dict(schema='gd.research.r62.physical_schedule_release.v1',rows=rows,available_at=d['planned_publication_at'],source_DTO_sha256=digest(d),source_SCED_event_id=wrapper['source_receipt']['run_id'],source_physical_sha256=sha(physical_path),physical_schedule_release=True,raw_PRICE_verified=True,financial_price_available=False,research_publication_allowed=False,formal_publication_allowed=False,execution_authority=False)
 result=assemble_slices(release,bridge,physical,window_count=window_count,start_index=start_index)
 for ref in result['references103']:
  ids=ref['physical_indices'];ref['renewable']={b:dict(SCED_reference_mw=math.fsum(physical['physical_cells'][j]['renewable'][b]['dispatched_mw'] for j in ids)/3,availability_mw=math.fsum(physical['physical_cells'][j]['renewable'][b]['availability_mw'] for j in ids)/3,curtailed_mw=math.fsum(physical['physical_cells'][j]['renewable'][b]['curtailed_mw'] for j in ids)/3,physical_indices=ids,price_eligible=True,eligibility_basis='explicit synthetic research; no financial entitlement') for b in wrapper['eligible_VRE']['nodes']}
 for path in [p/'PREPARATION.json',r/'PRICE_REVIEW.json',rawpath,Path(physical_path)]:pins[str(path.resolve())]=sha(path)
 result.update(physical_schedule_release=release,raw_DA_price98=prices,new_source_pins={str(i):dict(path=f,sha256=h) for i,(f,h) in enumerate(sorted(pins.items()))},finalizer_arguments=dict(price_prepared=str(p),price_review=str(r),physical_path=str(physical_path),price_native_paths=price_native_paths))
 return result
