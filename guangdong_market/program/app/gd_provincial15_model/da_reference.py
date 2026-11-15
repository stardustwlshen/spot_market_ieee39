"""AGC reference from real new provincial DA, not legacy acceptance-shaped files."""
from pathlib import Path
from gd_provincial_da_contract import verify,read,digest

def load(host):
 context=host.context;path=Path(context['provincial_DA_manifest']);proof=verify(path,require_price=True);m=read(path)
 if digest(path)!=context['provincial_DA_manifest_sha256']:raise ValueError('DA reference changed')
 from smm.gd_october_job_codec import loads
 case=loads(Path(m['model_case']['path']).read_bytes());st=m['stages']['SCED'];names=read(st['names']['path']);r=read(st['response']['path'])
 if len(names['variables'])!=len(r['primal']):raise ValueError('DA reference vector length mismatch')
 values=dict(zip(names['variables'],r['primal']));carry=read(host.run/'INITIAL_CARRY.json');days=read(carry['day_declaration_path']);day=days['days'][host.origin.date().isoformat()]
 pins={**m['source_pins'],str(path):digest(path),carry['day_declaration_path']:carry['day_declaration_sha256']}
 plan=dict(cutoff=day['available_at'],domain_start=host.origin.isoformat(),available_at=day['available_at'],cells=96,step_minutes=15,source_kind='DA_SCED',SCED_response_sha256=st['response']['sha256'],audit=dict(DA_SCED_accepted=True,SCED_full_KKT=st['KKT']['passed'],full_physical=True),source_pins=pins,source_authentication='new provincial DA 96x15 raw KKT and physical export')
 return case,values,plan
