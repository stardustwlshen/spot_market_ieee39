"""Reproducible full DA + RT96 acceptance using only detached frozen inputs.
No solver or physical relaxation parameter is changed. Source inputs are never written.
"""
from pathlib import Path
import argparse, json, os, shutil, sys, time, traceback
import yaml
p=argparse.ArgumentParser()
p.add_argument('--source',type=Path,required=True)
p.add_argument('--output',type=Path,required=True)
p.add_argument('--season',choices=['wet','dry'])
p.add_argument('--renewable',type=float,default=1.)
p.add_argument('--load',type=float,default=1.)
p.add_argument('--forecast-model',choices=['legacy_v1','multiscale_v2'])
a=p.parse_args(); sys.path.insert(0,str(a.source/'src'))
from smm.config import load_config
from smm.live_runner import atomic_json, capture_provenance, ProgressWriter
from smm.pipeline import prepare, write_outputs
from smm.full import run_full, write_full_outputs
from smm.typical_scenarios import fingerprint, prepare_typical
from smm.bids import bid_dir
start=time.perf_counter(); out=a.output; out.mkdir(parents=True,exist_ok=False)
source_before=fingerprint(a.source)
snap=out/'scenario_snapshot'
for folder in ['config','data','bids']:
 shutil.copytree(a.source/folder,snap/folder)
if a.forecast_model:
 basefile=snap/'config/base.yaml'; base=yaml.safe_load(basefile.read_text())
 base.setdefault('forecast',{})['model']=a.forecast_model
 basefile.write_text(yaml.safe_dump(base,sort_keys=False,allow_unicode=True))
if a.season or a.renewable!=1. or a.load!=1.:
 preview=prepare_typical(snap,season=a.season,renewable_multiplier=a.renewable,load_multiplier=a.load)
 basefile=snap/'config/base.yaml'; base=yaml.safe_load(basefile.read_text())
 base.update(season=preview['season'],load_scale=preview['load_scale'])
 basefile.write_text(yaml.safe_dump(base,sort_keys=False,allow_unicode=True))
 cfg=load_config(root=snap)
 names=['load']+[u['id'] for u in cfg.units['renewable']]
 cols=['period','time']+[f'{n}_{stage}' for n in names for stage in ['actual','da','id','rt']]
 preview['profiles'][cols].to_csv(snap/'data/profile_overrides.csv',index=False)
 atomic_json(bid_dir(cfg)/f"bids_v{preview['bids']['version']}.json",preview['bids'])
 atomic_json(out/'scenario_assessment.json',preview.get('assessment',{}))
cfg=load_config(root=snap)
assert cfg.base.get('tie_line_mode','supply')=='supply'
assert not cfg.mode.get('demand_bid')
assert cfg.mode['realtime']['storage']['anchor']=='two_hour_eq'
assert cfg.base['solver']['mip_rel_gap']==.01
assert cfg.mode['realtime']['mip_rel_gap']==.01
atomic_json(out/'acceptance_spec.json',dict(source=str(a.source),source_fingerprint=source_before,
 scenario={'season':cfg.base['season'],'renewable_multiplier':a.renewable,'load_multiplier':a.load},
 forecast_model=cfg.base.get('forecast',{}).get('model','legacy_v1'),
 solver=cfg.base['solver'],realtime_solver={k:cfg.mode['realtime'][k] for k in ['mip_rel_gap','time_limit_s']},
 empirical_typical_day_claim=False,planned_rt_periods=cfg.T,source_modified=False))
capture_provenance(out,snap,source_root=a.source)
progress=ProgressWriter(out,total=cfg.T)
da_written=False
try:
 case=prepare(cfg, read_only=True)
 atomic_json(out/'resolved_bids.json',case.bids);case.profiles.to_csv(out/'profiles.csv',index=False)
 def event(e):
  global da_written
  progress(e)
  if e['phase']=='da_done':
   (out/'da').mkdir(exist_ok=True);write_outputs(e['da'],out/'da',make_plots=True);da_written=True
 result=run_full(cfg,case,periods=cfg.T,progress_callback=event)
 summary=write_full_outputs(result,out,make_plots=True)
 atomic_json(out/'acceptance_status.json',dict(status='completed',executed_periods=len(result['rr'].rt),
  full_day=len(result['rr'].rt)==cfg.T,da_scuc_info=result['da'].scuc_info,
  da_sced_info=result['da'].sced_info,source_unchanged=fingerprint(a.source)==source_before,
  elapsed_s=time.perf_counter()-start,checks=result['checks']))
 print('ACCEPTANCE COMPLETE',out,flush=True)
except Exception as exc:
 atomic_json(out/'acceptance_status.json',dict(status='failed',error_type=type(exc).__name__,error=str(exc),
  source_unchanged=fingerprint(a.source)==source_before,elapsed_s=time.perf_counter()-start,
  executed_periods=progress.state.get('executed_periods',0),da_outputs_saved=da_written,full_day=False))
 traceback.print_exc(); raise
