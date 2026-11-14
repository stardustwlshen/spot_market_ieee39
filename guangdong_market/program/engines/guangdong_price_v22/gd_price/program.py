"""Portable PRICE preparation. Explicit source inputs; no implicit native execution."""
from pathlib import Path
import argparse,json
from domain import read,sha,need,write,prepare
from evidence import pair_inputs,seed_inputs
from mechanisms import soften
from mapping import build
B=Path(__file__).resolve().parent

def build_price(pair,case,agc,da_run,parameters,output,*,seed_request=None,boundary='DA_reference_R61_compatible_research'):
 out=Path(output);need(not out.exists(),'fresh output only')
 from source_contracts import selected_da
 selected_da(da_run)
 pars=read(parameters);need(pars['mode']=='research','formal route unavailable: official interpretation and parameter evidence unresolved')
 if seed_request:
  need(pair is None and boundary=='DA_reference_R61_compatible_research','seed is DA fallback only')
  inputs=seed_inputs(da_run,seed_request,case,agc);source=seed_request
 else:inputs=pair_inputs(pair);source=pair
 out.mkdir(parents=True);prepare(source,case,agc,da_run,out/'domain',research_current_sced=True,boundary=boundary,verified_inputs=inputs)
 q=read(out/'domain/template.json');n=read(out/'domain/names.json');ctx=read(out/'domain/CONTEXT.json');mapping=build(q,n)
 q,n,tr=soften(q,n,mapping,pars,as_of=ctx['cutoff']);ctx.update(seed_DA_fallback=bool(seed_request),physical_release=False,formal_bill=None,hard_network_no_official_slack_parameters=False,penalty_source_mode=pars['mode'])
 for f,z in [('template.json',q),('names.json',n),('CONTEXT.json',ctx),('mapping.json',mapping),('parameters.json',pars),('TRACE.json',tr)]:write(out/f,z)
 sources=[Path(case),Path(agc),Path(source),Path(parameters),*B.glob('*.py'),B/'REVIEWERS.json']
 write(out/'READY.json',dict(schema='gd.portable.PRICE.ready.v1',columns=len(q['variables']),rows=len(q['rows']),native_calls=0,artifact_pins={p.name:sha(p) for p in out.iterdir() if p.is_file()},source_pins={str(p.resolve()):sha(p) for p in sources},formal=False))
 return dict(status='prepared_not_solved',columns=len(q['variables']),rows=len(q['rows']),native_calls=0,start=ctx['start'],seed_DA_fallback=bool(seed_request))

def main():
 p=argparse.ArgumentParser();p.add_argument('--pair');p.add_argument('--seed-request');p.add_argument('--case',required=True);p.add_argument('--agc',required=True);p.add_argument('--da-run',required=True);p.add_argument('--parameters',required=True);p.add_argument('--output',required=True);p.add_argument('--boundary',choices=['DA_reference_R61_compatible_research','current_sced_research'],default='DA_reference_R61_compatible_research');a=p.parse_args();print(json.dumps(build_price(a.pair,a.case,a.agc,a.da_run,a.parameters,a.output,seed_request=a.seed_request,boundary=a.boundary)))
if __name__=='__main__':main()
