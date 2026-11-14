"""Exact consumed-field projection, not a hash cache or a replacement case."""
from copy import deepcopy
from hashlib import sha256
import math
from .gd_october_job_codec import dumps
FAMILIES=('load_mw','wind_mw','solar_mw','boundary_tie_mw')
LINE_FIELDS=('from_bus','to_bus','susceptance_mw_per_rad')
def need(ok,msg):
 if not ok:raise ValueError('R52 minimal source: '+msg)
def codec_sha(x):return sha256(dumps(x)).hexdigest()
def make_view(case):
 e=case['energy_inputs']
 need(set(('buses','reference_buses','lines','sections'))<=set(e),'missing original network field')
 lines={k:{f:deepcopy(z[f]) for f in LINE_FIELDS} for k,z in e['lines'].items()}
 return dict(schema='gd.research.r52.consumed_source_view.v1',energy_inputs=dict(buses=deepcopy(e['buses']),reference_buses=deepcopy(e['reference_buses']),lines=lines,sections=deepcopy(e['sections'])),contract=dict(sources={}),provenance=dict(original_case_codec_sha256=codec_sha(case),original_lines_codec_sha256=codec_sha(e['lines']),selection='only _forecast and PRICE network-provenance consumed fields; full original case validated before projection',original_files_still_stage_pinned=True,mutable_view_fully_digested_every_validation=True))
def validate_view(v):
 need(set(v)=={'schema','energy_inputs','contract','provenance'},'exact view fields')
 need(v['schema']=='gd.research.r52.consumed_source_view.v1','schema')
 e=v['energy_inputs'];need(set(e)=={'buses','reference_buses','lines','sections'},'network fields')
 need(len(e['buses'])==len(set(e['buses'])) and set(e['reference_buses'])<=set(e['buses']),'bus inventory')
 for z in e['lines'].values():
  need(set(z)==set(LINE_FIELDS),'exact topology fields')
  need(z['from_bus'] in e['buses'] and z['to_bus'] in e['buses'] and type(z['susceptance_mw_per_rad']) in (int,float) and math.isfinite(z['susceptance_mw_per_rad']),'topology')
 need(set(v['contract'])=={'sources'} and set(v['contract']['sources'])=={'prior:'+f for f in FAMILIES},'four complete prior sources')
 for z in v['contract']['sources'].values():
  need({'artifact','sha256','available_at'}<=set(z),'prior bytes/hash/vintage required')
  need(type(z['artifact']) is bytes and sha256(z['artifact']).hexdigest()==z['sha256'],'prior raw bytes changed')
 p=v['provenance'];need(p['original_files_still_stage_pinned'] is True and p['mutable_view_fully_digested_every_validation'] is True,'no checks removed')
 for k in ('original_case_codec_sha256','original_lines_codec_sha256'):need(type(p[k]) is str and len(p[k])==64,'original source hashes')
 return True
