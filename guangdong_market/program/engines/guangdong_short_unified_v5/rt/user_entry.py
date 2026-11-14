"""Portable short RT v2 entry. Research day declarations require explicit opt-in/time inputs."""
from pathlib import Path
from datetime import datetime,timedelta
import argparse,sys,json,hashlib,importlib.util,tempfile,subprocess
ROOT=Path(__file__).resolve().parent
CORE=ROOT/'guangdong_rt_short_user_cli_v5_20261112/program.py'
PROFILE='repeat-selected-da-terminal-and-48h-online-plan-research'
read=lambda p:json.loads(Path(p).read_bytes())
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
def declare(args):
 p=argparse.ArgumentParser();p.add_argument('--da',required=True,type=Path);p.add_argument('--output',required=True,type=Path);p.add_argument('--profile',choices=[PROFILE],required=True);p.add_argument('--protection-policy',choices=['conservative_DA_protection_research'],required=True);p.add_argument('--hypothetical-published-at',required=True);p.add_argument('--hypothetical-available-at',required=True);a=p.parse_args(args)
 assert not a.output.exists(),'output exists; no overwrite'
 adapter=ROOT/'guangdong_rt_short_selected_da_adapter_v1_20261112/seed.py';s=importlib.util.spec_from_file_location('_selected_seed',adapter);m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
 with tempfile.TemporaryDirectory(prefix='gd-short-declare-') as temp:
  out=Path(temp)/'proof'
  import contextlib,io
  with contextlib.redirect_stdout(io.StringIO()):m.prepare(a.da,out)
  from smm.gd_october_job_codec import loads
  seed=out/'seed';case=loads((seed/'case.json').read_bytes());r=read(seed/'request.json');w=read(seed/'WARMSTART_SOURCE.json');origin=datetime.fromisoformat(r['origin']);cut=datetime.fromisoformat(r['cutoff']);pub=datetime.fromisoformat(a.hypothetical_published_at);avail=datetime.fromisoformat(a.hypothetical_available_at)
  assert pub.utcoffset()==avail.utcoffset()==timedelta(hours=8),'explicit UTC+08:00 required'
  assert pub<=avail<=cut,'hypothetical publication/availability must not exceed seed cutoff'
  assert origin.hour==origin.minute==origin.second==0,'midnight origin required'
  z=dict(schema='gd.short.day_declarations.research.v1',mode='hypothetical_published_research',profile=a.profile,protection_policy=a.protection_policy,protection_scope='Research-only whole-day conservative DA protection; not a verified official general RT-SCUC rule. Commercial awards, reliability plan, must-run intervals, issued locks and safety-change permission are not interchangeable.',description='Explicit independent research assumption: selected accepted48h DA online plan is assumed wholly published at the entered time; repeat its original declared terminal target for D and D+1. Not verified publication, operating telemetry or an official default; no future optimized SOC used.',days={})
  for offset in range(2):
   date=(origin+timedelta(days=offset)).date().isoformat();artifact=dict(thermal_online={g:r['DA_online'][g][288*offset:288*(offset+1)] for g in case['energy_inputs']['thermal']},storage={})
   assert all(len(v)==288 for v in artifact['thermal_online'].values()),'selected DA lacks explicit48h online plan'
   for u,b in case['batteries'].items():
    assert date in b['cycle_budget'],'selected source lacks dated cycle budget'
    artifact['storage'][u]=dict(day_end_at=(origin+timedelta(days=offset+1)).isoformat(),energy_mwh=b['terminal_energy_mwh'],limit_cycles=b['cycle_budget'][date]['limit_cycles'],transform='repeat original selected declared terminal number, not optimized future SOC',original_terminal_at=b['terminal_at'],original_terminal_energy_mwh=b['terminal_energy_mwh'])
   digest=hashlib.sha256(json.dumps(artifact,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
   z['days'][date]=dict(basis='explicit_new_research_assumption',published_at=pub.isoformat(),available_at=avail.isoformat(),source=dict(case_sha256=sha(seed/'case.json'),accepted_original_DA_response_sha256=w['response_sha256'],selected_DA=str(a.da.resolve()),selected_DA_run_id=read(a.da/'RUN.json')['run_id'],actual_publication_verified=False,constructor_sha256=sha(__file__)),artifact=artifact,artifact_sha256=digest)
 with a.output.open('x') as f:json.dump(z,f,indent=2,allow_nan=False)
 return dict(status='explicit_research_declarations_created',path=str(a.output.resolve()),sha256=sha(a.output),native_calls=0,formal=False,safety_release=False,profile=a.profile,targets={d:{u:v['energy_mwh']for u,v in x['artifact']['storage'].items()} for d,x in z['days'].items()})
if __name__=='__main__':
 try:
  if len(sys.argv)>1 and sys.argv[1]=='declare':print(json.dumps(declare(sys.argv[2:])))
  else:
   if len(sys.argv)>1 and sys.argv[1]=='seed':
    assert '--day-declarations' in sys.argv,'seed requires explicit --day-declarations; no hidden operating default'
    declaration=read(Path(sys.argv[sys.argv.index('--day-declarations')+1]));assert declaration.get('protection_policy')=='conservative_DA_protection_research','explicit conservative DA research protection policy required; formal scope unresolved'
   raise SystemExit(subprocess.call([sys.executable,'-B',str(CORE),*sys.argv[1:]]))
 except Exception as e:print(json.dumps(dict(status='blocked',reason=str(e),native_calls=0)));raise SystemExit(1)
