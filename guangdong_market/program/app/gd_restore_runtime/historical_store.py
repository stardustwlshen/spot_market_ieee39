"""Explicit, immutable historical-object access. Not a global filesystem hook."""
from pathlib import Path
import hashlib,json,gzip
class HistoricalStore:
 def __init__(self,manifest,manifest_sha256):
  self.manifest=Path(manifest);raw=self.manifest.read_bytes()
  if hashlib.sha256(raw).hexdigest()!=manifest_sha256:raise ValueError('import manifest changed')
  self.data=json.loads(raw);self.digest=manifest_sha256;self.read_log=[]
 def _record(self,logical,expected=None):
  versions=self.data['map'].get(str(logical),{})
  if expected is None:
   if len(versions)!=1:raise ValueError('missing or ambiguous historical version; expected SHA required')
   expected=next(iter(versions))
  if expected not in versions:raise ValueError('unmapped historical identity')
  return expected,versions[expected]
 def bytes(self,logical,expected=None):
  h,d=self._record(logical,expected);p=Path(d['path'])
  if p.is_symlink()or not p.is_file():raise ValueError('historical object must be regular file')
  data=p.read_bytes()
  if len(data)!=d['bytes']or hashlib.sha256(data).hexdigest()!=h:raise ValueError('historical object changed')
  self.read_log.append({'logical':str(logical),'sha256':h,'physical':str(p)})
  return data
 def json(self,logical,expected=None):
  b=self.bytes(logical,expected);return json.loads(gzip.decompress(b)if str(logical).endswith('.gz')else b)
 def verify_pins(self,pins):
  for p,h in pins.items():self.bytes(p,h)
 def same_identity(self,actual,expected,expected_sha):
  if str(actual)!=str(expected):raise ValueError('logical source identity mismatch')
  self.bytes(actual,expected_sha);return True
 def export_descriptor(self):
  # Child process reconstructs its own store and verifies the same manifest bytes.
  return {'manifest':str(self.manifest.resolve()),'manifest_sha256':self.digest}
