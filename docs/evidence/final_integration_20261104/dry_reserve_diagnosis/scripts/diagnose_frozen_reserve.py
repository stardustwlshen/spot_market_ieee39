"""Bounded counterfactual: frozen dry profiles, DA reserve only, unchanged RT/physics."""
from pathlib import Path
import argparse, json, shutil, sys, time, traceback, hashlib
import pandas as pd
p=argparse.ArgumentParser();p.add_argument('--source',type=Path,required=True);p.add_argument('--out',type=Path,required=True);p.add_argument('--buffer',type=float,required=True);p.add_argument('--code-root',type=Path,default=Path(__file__).resolve().parents[1]);a=p.parse_args()
sys.path.insert(0,str(a.code_root/'src'))
from smm.config import load_config
from smm.network import build_network
from smm.case import build_da_case
from smm.live_runner import atomic_json,capture_provenance,ProgressWriter
from smm.full import run_full,write_full_outputs
from smm.pipeline import write_outputs
assert a.buffer in (100.,250.)
a.out.mkdir(parents=True,exist_ok=False);snap=a.out/'scenario_snapshot';shutil.copytree(a.source/'scenario_snapshot',snap)
for f in ['profiles.csv','resolved_bids.json']:shutil.copyfile(a.source/f,a.out/f)
cfg=load_config(root=snap); profiles=pd.read_csv(a.out/'profiles.csv'); bids=json.loads((a.out/'resolved_bids.json').read_text());c=build_da_case(cfg,build_network(cfg),profiles,bids)
assert cfg.base['solver']['mip_rel_gap']==.01 and cfg.base['solver']['time_limit_s']==600
assert cfg.mode['realtime']['mip_rel_gap']==.01 and cfg.mode['realtime']['storage']['anchor']=='two_hour_eq'
old_up=c.R_up.copy();c.R_up=c.R_up+a.buffer;c.reserve_info['diagnostic_DA_buffer_mw']=a.buffer
atomic_json(a.out/'counterfactual_spec.json',dict(source=str(a.source),source_code=str(a.code_root),DA_extra_reserve_mw=a.buffer,RT_requirement_unchanged=True,original_R_up=old_up.tolist(),DA_R_up=c.R_up.tolist(),solver=cfg.base['solver'],realtime=cfg.mode['realtime'],scope='Diagnostic scheduling change, not a new default. Same frozen profiles, bids, physical constraints and penalties.',snapshot_identical_to_source=True))
capture_provenance(a.out,snap,source_root=a.code_root); progress=ProgressWriter(a.out,total=96);start=time.perf_counter()
def event(e):
 progress(e)
 if e['phase']=='da_done':
  (a.out/'da').mkdir(exist_ok=True);write_outputs(e['da'],a.out/'da',make_plots=False)
try:
 res=run_full(cfg,c,periods=96,progress_callback=event);s=write_full_outputs(res,a.out,make_plots=False)
 atomic_json(a.out/'counterfactual_status.json',dict(status='completed',elapsed_s=time.perf_counter()-start,executed_periods=len(res['rr'].rt),da_scuc_info=res['da'].scuc_info,checks=res['checks']))
 print('DONE',a.out,flush=True)
except Exception as e:
 atomic_json(a.out/'counterfactual_status.json',dict(status='failed',elapsed_s=time.perf_counter()-start,error_type=type(e).__name__,error=str(e),executed_periods=progress.state.get('executed_periods',0)));traceback.print_exc();raise
