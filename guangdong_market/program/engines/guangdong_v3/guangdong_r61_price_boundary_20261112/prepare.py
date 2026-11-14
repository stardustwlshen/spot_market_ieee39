"""Standalone PRICE construction from one accepted actual R60 RT input. No native."""
from pathlib import Path
import json,hashlib,sys,argparse
B=Path(__file__).resolve().parent;R=B.parent
sys.path[:0]=[str(R/'guangdong_r52_minimal_source_view_20261112'),str(R/'guangdong_r46_curtailable_full_day_20261112')]
from r52smm import gd_r46_day_models as models,highs_solver as hs
from r52smm.gd_r15_da_rt_bridge import digest
from transform import transform
read=lambda p:json.loads(Path(p).read_bytes())
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
def write(p,x):p.write_text(json.dumps(x,sort_keys=True,separators=(',',':'),allow_nan=False))
def prepare(prepared,native,accepted,output):
 prepared,native,accepted,output=map(Path,(prepared,native,accepted,output));ticket=read(prepared/'TICKET.json');inp=read(prepared/'input.json');a=read(accepted/'ACCEPTANCE.json')
 assert ticket['schema']=='gd.research.r60.prepared_ticket.v1' and a['schema']=='gd.research.r60.RT_accepted.v1'
 checkpoint=Path(ticket['checkpoint_path']);cp=read(checkpoint)
 assert sha(checkpoint)==ticket['checkpoint_sha256'] and cp['source_identity']['sha256']==ticket['source_identity_sha256']
 for path,h in cp['source_identity']['pins'].items():assert sha(path)==h
 assert a['KKT']['passed'] is True and a['physical_cells_reintegrated']==24
 w=inp['window']['index'];assert 46<=w<=95 and ticket['window']==a['window']==w
 assert digest(inp)==ticket['input_sha256'];assert read(native/'response.json')['status']=='optimal'
 for f,k in [('template.json','template_sha256'),('names.json','names_sha256')]:assert digest(read(prepared/f))==ticket[k]
 assert {k:v for k,v in read(native/'request.json').items()if k!='deadline'}==read(prepared/'template.json')
 for role,z in a['event_pins'].items():assert sha(z['path'])==z['sha256']
 assert a['event_pins']['response']['sha256']==sha(native/'response.json')
 assert all(z['bid_proxy_yuan_mwh']==0 for z in inp['renewable'].values())
 m,audit=models.build_price_template(inp);q,vs,cs=hs.serialize_model(m,dict(threads=1,time_limit=60.,mip_rel_gap=.001,mip_abs_gap=0.,output_flag=False,presolve='choose',solve_relaxation=False))
 q.pop('deadline',None);q['dual']=True;q['preparation_elapsed_s']=0.;names=dict(variables=[v.name for v in vs],constraints=[c.name for c in cs]);out,newnames,tr=transform(q,names,inp)
 output.mkdir(parents=True,exist_ok=False)
 pins={str(p.resolve()):sha(p)for p in [checkpoint,prepared/'TICKET.json',prepared/'input.json',prepared/'template.json',native/'request.json',native/'response.json',accepted/'ACCEPTANCE.json']}
 for z in a['event_pins'].values():pins[str(Path(z['path']).resolve())]=z['sha256']
 tr.update(window=w,source_pins=pins,source_kind='accepted_R60_actual_RT_input_same_R42_bid0_DA_reference',base_model_audit=audit,DA_tail_source='same48h_research_physical_reference_not_official_next_day_publication')
 for f,x in [('input.json',inp),('base_template.json',q),('base_names.json',names),('template.json',out),('names.json',newnames),('transformation.json',tr)]:write(output/f,x)
 meta=dict(window=w,native_calls=0,formal_publication_allowed=False,execution_authority=False,source_pins=pins,artifacts={p.name:sha(p)for p in output.iterdir()},source_code_pins={**cp['source_identity']['pins'],**{str(p.resolve()):sha(p)for p in B.glob('*.py')}},columns=len(out['variables']),rows=len(out['rows']))
 write(output/'PREPARATION.json',meta);print(json.dumps(dict(window=w,rows=meta['rows'],columns=meta['columns'],preparation_sha256=sha(output/'PREPARATION.json'))));return meta
if __name__=='__main__':
 ap=argparse.ArgumentParser();[ap.add_argument('--'+x,required=True)for x in ('prepared','native','accepted','output')];a=ap.parse_args();prepare(a.prepared,a.native,a.accepted,a.output)
