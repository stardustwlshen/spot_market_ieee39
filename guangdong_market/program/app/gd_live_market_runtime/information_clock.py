"""Declared input availability for new research prices; no invented receipt times.
This is an information-use guard, not official market or safety certification.
"""
from datetime import datetime
import hashlib,json,re
from pathlib import Path

def digest(value):
 return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
def clock(value):
 if not isinstance(value,str):raise ValueError('explicit timezone-aware clock required')
 t=datetime.fromisoformat(value)
 if t.utcoffset() is None:raise ValueError('timezone-aware clock required')
 return t
def bindings(source):
 r=source['request']; pins=source['source_pins']; da=Path(source['selected_DA'])/'ARTIFACTS.json'
 da_hash=pins.get(str(da)) or pins.get(str(da.resolve()))
 if not isinstance(da_hash,str) or not re.fullmatch('[0-9a-f]{64}',da_hash):raise ValueError('selected DA artifact not pinned')
 result={'selected_DA':digest({'path':str(da),'sha256':da_hash}), 'physical_SCED':source['accepted']['SCED_response_sha256'],'effective_AGC':digest(source['effective_agc'])}
 for key,value in r['sources'].items():result['request:'+key]=digest(value)
 ref=source.get('reference_revision')
 if ref is not None:result['reference_revision']=digest(ref)
 return result

def validate(source,declaration):
 """None stays unknown. Explicit invalid/late-for-preclear declarations fail closed."""
 r=source['request']; base=dict(schema='gd.price.input_availability.v1',run_id=source['branch_id'],quarter=source['request']['delivery_tick'],SCUC_data_cutoff=r['cutoff'],pricing_as_of=None,pricing_input_available_at=None,status='unknown',compute_completed_at=None,publication_at=None,scope='research_only',formal_use_allowed=False,preclear_use_allowed=False)
 if declaration is None:return dict(base,missing='explicit pricing_information_clock declaration')
 if not isinstance(declaration,dict):raise ValueError('information clock must be a mapping')
 if declaration.get('schema')!='gd.price.input_availability.declaration.v1':raise ValueError('information clock schema')
 if (declaration.get('run_id'),declaration.get('quarter'),declaration.get('SCUC_data_cutoff'))!=(base['run_id'],base['quarter'],r['cutoff']):raise ValueError('information clock run/quarter/cutoff mismatch')
 purpose=declaration.get('purpose')
 if purpose not in ('research_read','research_preclear'):raise ValueError('no formal price authorization from research clock')
 at=clock(declaration.get('pricing_as_of')); cutoff=clock(r['cutoff']); required=bindings(source); entries=declaration.get('inputs')
 if not isinstance(entries,dict) or set(entries)!=set(required):raise ValueError('complete input availability inventory required: '+','.join(sorted(required)))
 times=[]
 for role,h in required.items():
  row=entries[role]
  if not isinstance(row,dict) or row.get('binding_sha256')!=h:raise ValueError('availability binding mismatch: '+role)
  if row.get('basis') not in ('research_assumption','recorded_receipt') or not isinstance(row.get('evidence'),str) or not row['evidence'].strip():raise ValueError('availability basis/evidence required: '+role)
  if row.get('basis')=='recorded_receipt':
   path=Path(row.get('receipt_path',''));expected=row.get('receipt_sha256')
   if source['source_pins'].get(str(path))!=expected or not isinstance(expected,str) or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=expected:raise ValueError('recorded receipt must be pinned actual bytes: '+role)
   receipt=json.loads(path.read_bytes())
   if (receipt.get('run_id'),receipt.get('quarter'),receipt.get('role'),receipt.get('binding_sha256'),receipt.get('available_at'))!=(source['branch_id'],source['request']['delivery_tick'],role,h,row.get('available_at')):raise ValueError('recorded receipt binding mismatch: '+role)
  t=clock(row.get('available_at'));times.append(t)
  if t>at:raise ValueError('input unavailable at pricing_as_of: '+role)
 ref=source.get('reference_revision')
 if ref is not None:
  known=clock(ref.get('input_available_at'))
  if clock(entries['reference_revision']['available_at'])<known:raise ValueError('declared reference availability predates bound reference')
 latest=max(times)
 if purpose=='research_preclear' and (at>cutoff or latest>cutoff):raise ValueError('post-cutoff information cannot be used for preclear')
 return dict(base,status='retrospective_research' if latest>cutoff else 'declared_research_clock',pricing_as_of=at.isoformat(),pricing_input_available_at=latest.isoformat(),declaration_sha256=digest(declaration),bindings=required,inputs=entries,purpose=purpose,research_preclear_allowed=purpose=='research_preclear',not_retroactively_available=True)

def require_prepared(context):
 z=context.get('pricing_information_clock')
 if not isinstance(z,dict) or z.get('status') not in ('declared_research_clock','retrospective_research'):raise ValueError('new PRICE solve requires complete declared input availability; historical viewing remains permitted')
 if clock(z['pricing_input_available_at'])>clock(z['pricing_as_of']):raise ValueError('pricing inputs unavailable')
 return z

def research_policy(evidence):
 """UI must obtain an explicit selection; calling this is not a silent default."""
 if not isinstance(evidence,str) or not evidence.strip():raise ValueError('research assumption explanation required')
 return dict(schema='gd.price.research_clock_policy.v1',assume_base_inputs_available_at_dispatch_cutoff=True,as_of_policy='dispatch_cutoff_or_later_declared_reference',purpose='research_read',evidence=evidence,official=False)

def declaration_for(source,policy):
 if policy.get('schema')=='gd.price.input_availability.declaration.v1':return policy
 if policy.get('schema')=='gd.price.input_availability.schedule.v1':return policy.get('quarters',{}).get(str(source['request']['delivery_tick']))
 if (policy.get('schema')!='gd.price.research_clock_policy.v1' or policy.get('assume_base_inputs_available_at_dispatch_cutoff') is not True or policy.get('as_of_policy')!='dispatch_cutoff_or_later_declared_reference' or policy.get('purpose')!='research_read' or policy.get('official') is not False or not isinstance(policy.get('evidence'),str) or not policy['evidence'].strip()):raise ValueError('explicit supported research availability policy required')
 cutoff=source['request']['cutoff']; at=clock(cutoff); rows={}
 for role,h in bindings(source).items():
  available=cutoff
  if role=='reference_revision':
   available=source['reference_revision']['input_available_at'];at=max(at,clock(available))
  rows[role]=dict(binding_sha256=h,available_at=available,basis='research_assumption',evidence=policy['evidence']+('; bound reference clock, not observed operations' if role=='reference_revision' else '; explicitly assumed available at dispatch cutoff, not measured receipt'))
 return dict(schema='gd.price.input_availability.declaration.v1',run_id=source['branch_id'],quarter=source['request']['delivery_tick'],SCUC_data_cutoff=cutoff,pricing_as_of=at.isoformat(),purpose='research_read',inputs=rows)
