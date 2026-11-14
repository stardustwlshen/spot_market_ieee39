"""Manifest-pinned augmentation of numeric checks only; raw/source checks unchanged."""
from pathlib import Path
import sys,importlib.util,hashlib
B=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('_gd_installed_network_certificate',B/'network_certificate.py');N=importlib.util.module_from_spec(spec);sys.modules[spec.name]=N;spec.loader.exec_module(N)
def select(q,r,n):
 b=N.certify(q,n,r);return b['derived_response']if b['derived_response']is not None else r,b

def metadata(q,r,n):
 _,b=select(q,r,n)
 return dict(SCED_response_kind=b['certificate']['kind'],original_native_response_sha256=N.digest(r),dual_certificate=b['certificate'],dual_certificate_sha256=b['certificate_sha256'],derived_response_sha256=b['certificate']['derived_response_sha256'],native_dual_passed=b['certificate']['native_KKT']['passed'],numeric_only_not_source_authority=True)

def wrap_checks(original):
 def checks(q,r,n,mip):
  if mip:return original(q,r,n,mip)
  numeric,b=select(q,r,n)
  result=original(q,numeric,n,mip) # Retained original audit/KKT still runs in full.
  if b['derived_response']is not None:
   result=dict(result,dual_certificate=b['certificate'],dual_certificate_sha256=b['certificate_sha256'],SCED_response_kind=b['certificate']['kind'],native_dual_passed=False)
  return result
 checks._gd_network_certificate_original=original
 return checks

def install(program):
 # Idempotent and confined to this known host's program/engine globals. Never alters source files.
 for owner in (program,program.engine):
  original=owner.checks
  if not hasattr(original,'_gd_network_certificate_original'):owner.checks=wrap_checks(original)
 return program

def pins():return {str(p.resolve()):hashlib.sha256(p.read_bytes()).hexdigest()for p in B.glob('*.py')}

def validate_record(actual,proof):
 """Compare stored new acceptance metadata, never authorize using its claimed pass.
 Old native-passed records remain compatible. Derived witnesses require explicit
 new metadata: a missing or changed certificate cannot be silently regenerated.
 """
 import json
 p=Path(actual)/'ACCEPTED_PAIR.json'
 record=json.loads(p.read_bytes())
 keys=('SCED_response_kind','dual_certificate','dual_certificate_sha256','native_dual_passed')
 required=proof['SCED_response_kind']=='native_primal_independent_derived_dual' or record.get('schema')=='gd.event.host.accepted_pair.v2' or any(k in record for k in keys)
 if required:
  N.need(all(k in record and record[k]==proof[k]for k in keys),'stored dual certificate differs from recomputed full proof')
 rawpath=(Path(actual)/'SCED/response.json.gz').resolve();rawsha=hashlib.sha256(rawpath.read_bytes()).hexdigest()
 if required or 'SCED_response_sha256'in record:
  N.need(record.get('SCED_response_sha256')==rawsha,'stored original native SCED hash mismatch')
 else:
  N.need(record.get('source_pins',{}).get(str(rawpath))==rawsha,'legacy original native SCED source pin absent or mismatched')
 return True
