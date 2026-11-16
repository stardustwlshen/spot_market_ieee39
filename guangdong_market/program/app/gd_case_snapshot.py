"""One prepare's verified case bytes and private conversion template.
Not a persistent cache, authorization token, or shared mutable model.
"""
from pathlib import Path
from copy import deepcopy
import hashlib,os,threading

def _sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()

class CaseSnapshot:
 __slots__=('__path','__raw','__template','__baseline','__codes','__owner')
 def __init__(self,path,expected_sha256,source_pins):
  from smm import gd_october_job_codec as codec
  from gd_provincial15_model import adapter
  import gd_forecast_inputs
  path=Path(path).resolve();raw=path.read_bytes();actual=hashlib.sha256(raw).hexdigest()
  if actual!=expected_sha256 or source_pins.get(str(path))!=actual:raise ValueError('case snapshot must match request and accepted source pins')
  codes={str(Path(m.__file__).resolve()):_sha(m.__file__)for m in (codec,adapter,gd_forecast_inputs)}
  codes[str(Path(__file__).resolve())]=_sha(__file__)
  for code_path,code_hash in codes.items():
   if code_path in source_pins and source_pins[code_path]!=code_hash:raise ValueError('case snapshot code conflicts with accepted source pin')
  template=adapter.convert_full_case(codec.loads(raw))
  self.__path=path;self.__raw=raw;self.__template=template;self.__baseline=gd_forecast_inputs.baseline_digest(template);self.__codes=codes;self.__owner=(os.getpid(),threading.get_ident())
  self.verify(path)
 def verify(self,path):
  if (os.getpid(),threading.get_ident())!=self.__owner:raise ValueError('case snapshot belongs to another prepare owner')
  if Path(path).resolve()!=self.__path:raise ValueError('case snapshot source path mismatch')
  if Path(path).read_bytes()!=self.__raw:raise ValueError('case bytes changed within prepare; new preparation required')
  for p,h in self.__codes.items():
   if _sha(p)!=h:raise ValueError('case snapshot conversion code changed')
 def source_pins(self,path):
  self.verify(path);return dict(self.__codes)
 def clone(self,path):
  self.verify(path);return deepcopy(self.__template)
 def baseline(self,path):
  self.verify(path);return self.__baseline

def require_snapshot(value):
 if type(value)is not CaseSnapshot:raise ValueError('exact internal CaseSnapshot required')
 return value
