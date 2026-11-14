"""Private-process full-content proof cache; no persisted verdict import."""
from pathlib import Path
from copy import deepcopy
import hashlib,json,os,zlib,sys

def content(*objects):
 return json.dumps(objects,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
def fingerprint(*objects):return hashlib.sha256(content(*objects)).hexdigest()
from content_hash import sha
_ACTIVE=None
def session_creation_guard():
 if _ACTIVE is not None:raise ValueError('proof memo worker forbids additional sessions')
def session_access_guard(session):
 if _ACTIVE is not None and (os.getpid()!=_ACTIVE.pid or session is not _ACTIVE.h.session):raise ValueError('foreign session in single-owner proof memo worker')
def install_for_pipeline(pipeline):
 from session import Pipeline
 if type(pipeline)is not Pipeline:raise ValueError('normal Pipeline only')
 h=pipeline.host
 if h.window!=0 or h.pending is not None or h.accepted or pipeline.prices.prepared or pipeline.prices.accepted:raise ValueError('memo requires fresh worker before first PRICE')
 memo=InstanceCache(h);pipeline.proof_memo=memo;return memo

class InstanceCache:
 def __init__(self,host):
  global _ACTIVE
  if _ACTIVE is not None:raise ValueError('only one memo owner per worker')
  from runner import GeneralRollingHost
  if not isinstance(host,GeneralRollingHost):raise ValueError('registered General host required')
  from v6_host import e
  from gd_post_scuc_runtime import consumers
  import loss_consumers
  if id(host.session.program) in loss_consumers._INSTALLED or loss_consumers._OWNED:raise ValueError('loss consumers already active; memo cannot replace them')
  host.session.verify_sources()
  self.pid=os.getpid()
  self.h=host;self.identity=(id(host),id(host.session),host.branch_id);self.path=Path(__file__).resolve();self.code_sha=sha(self.path);host.session.registry['source_pins'][str(self.path)]=self.code_sha
  self.stats=dict(pair_full=0,pair_hits=0,stage_full=0,stage_hits=0,phase_indexed=0,phase_hits=0)
  self.pairs={};self.stages={};self.phase_index={};self.indexed=set();self.e=e;self.consumers=consumers
  program=host.session.program
  owner=program # identity-preserving precision bridge does not introduce a proxy owner
  # Bypass only repeated linear scan, not phase verification or original validator.
  self.base_pair=consumers._ORIGINAL.get(id(owner),(None,e.verify_pair))[1]
  self.base_checks=owner.checks
  engine_checks=getattr(getattr(owner,'engine',None),'checks',None)
  if engine_checks is not None:
   left=getattr(self.base_checks,'_gd_network_certificate_original',self.base_checks);right=getattr(engine_checks,'_gd_network_certificate_original',engine_checks)
   if left is not right:raise ValueError('distinct mathematical validators cannot share a memo')
  _ACTIVE=self
  # Preserve the installed certificate-wrapper identity across repeated installed() calls.
  def checks(q,r,n,mip):return self.checks(q,r,n,mip)
  checks._gd_network_certificate_original=getattr(self.base_checks,'_gd_network_certificate_original',self.base_checks)
  self.checks_facade=checks;self.validator_owner=owner
  e.verify_pair=self.verify_pair;owner.checks=checks;program.checks=checks
  if engine_checks is not None:owner.engine.checks=checks
 def verify(self):
  session_access_guard(self.h.session)
  if _ACTIVE is not self:raise ValueError('memo owner registry changed')
  if self.identity!=(id(self.h),id(self.h.session),self.h.branch_id):raise ValueError('proof cache owner changed')
  if sha(self.path)!=self.code_sha:raise ValueError('proof cache code changed')
  self.h.session.verify_sources()
 def checks(self,q,r,n,integer):
  self.verify();payload=content(q,r,n,integer);key=hashlib.sha256(payload).hexdigest()
  if key in self.stages:
   old,result=self.stages[key]
   if zlib.decompress(old)!=payload:raise ValueError('stage fingerprint collision')
   self.stats['stage_hits']+=1;return deepcopy(result)
  result=self.base_checks(q,r,n,integer);self.stages[key]=(zlib.compress(payload,1),deepcopy(result));self.stats['stage_full']+=1;return result
 def _index(self):
  c=self.consumers
  for key,(record,payload,k,names_bytes) in self.phase_index.items():
   if c._PHASES.get(k)is not record:raise ValueError('phase registration deleted/replaced')
   c.verified_state(record) # fail closed on original raw/code/record drift even on miss
   if content(record['phase'].candidate['names'])!=names_bytes:raise ValueError('registered names drift')
  for k,record in c._PHASES.items():
   if k in self.indexed:continue
   if record['phase'].h is not self.h:raise ValueError('foreign phase in single-owner memo worker')
   c.verified_state(record);p=record['phase'].p;a=p.parent/'actual'
   objects=[self.e.read(a/f'{stage}/{name}.json.gz')for stage,name in [('SCUC','request'),('SCUC','response'),('SCED','request'),('SCED','response')]]
   objects.append(record['phase'].candidate['names']);payload=content(*objects);key=hashlib.sha256(payload).hexdigest()
   if key in self.phase_index and self.phase_index[key][0]is not record:raise ValueError('ambiguous phase fingerprint')
   self.pairs.pop(key,None) # newly registered phase carries stronger source proof
   self.phase_index[key]=(record,zlib.compress(payload,1),k,content(objects[-1]));self.indexed.add(k);self.stats['phase_indexed']+=1
 def verify_pair(self,q,u,eq,r,n):
  self.verify();self._index();payload=content(q,u,eq,r,n);key=hashlib.sha256(payload).hexdigest()
  match=self.phase_index.get(key)
  if match is not None:
   record,original,registry_key,names_bytes=match
   if payload!=zlib.decompress(original):raise ValueError('phase fingerprint collision/content mismatch')
   self.consumers.verified_state(record)
  if key in self.pairs:
   original,result=self.pairs[key]
   if zlib.decompress(original)!=payload:raise ValueError('pair fingerprint collision/content mismatch')
   self.stats['pair_hits']+=1;return deepcopy(result)
  if match is not None:result=self.consumers.proof(record);self.stats['phase_hits']+=1
  else:result=self.base_pair(q,u,eq,r,n);self.stats['pair_full']+=1
  self.pairs[key]=(zlib.compress(payload,1),deepcopy(result));return result
