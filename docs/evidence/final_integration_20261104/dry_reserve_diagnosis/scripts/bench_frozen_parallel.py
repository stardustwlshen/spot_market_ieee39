"""One bounded DA strategy trial; prepare does not solve. No physical/economic changes."""
from pathlib import Path
import argparse,copy,hashlib,json,shutil,subprocess,sys,time,traceback
import numpy as np,pyomo.environ as pyo,highspy
ap=argparse.ArgumentParser();ap.add_argument('--mode',choices=['prepare','run'],required=True);ap.add_argument('--runtime-load-note',default='');ap.add_argument('--source-run',type=Path,default=Path('/workspace/scratch/42c5fe32dad0/ieee39-final-evidence/pv2152_dry_lowre_highload_full96'));ap.add_argument('--out',type=Path,default=Path('/workspace/scratch/42c5fe32dad0/ieee39-dry-reserve-evidence/parallel_on_DA'));ap.add_argument('--code-root',type=Path,default=Path(__file__).resolve().parents[1]);a=ap.parse_args()
ROOT=a.code_root;sys.path.insert(0,str(ROOT/'src'))
from smm.config import load_config
from smm.pipeline import prepare,write_outputs
from smm.da import run_da
from smm import scuc
from smm.live_runner import atomic_json,capture_provenance
P=a.source_run;O=a.out
def source_commit():
 try:return subprocess.check_output(['git','-C',str(ROOT),'rev-parse','HEAD'],text=True).strip()
 except Exception:return 'unavailable'
def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def input_hashes(p):return {str(f.relative_to(p)):digest(f) for f in sorted(p.rglob('*')) if f.is_file()}
def model_counts(m):return dict(variables=sum(1 for _ in m.component_data_objects(pyo.Var)),unfixed_integer_variables=sum(v.is_integer() and not v.fixed for v in m.component_data_objects(pyo.Var)),constraints=sum(1 for _ in m.component_data_objects(pyo.Constraint,active=True)))
def cfg_case(root):
 cfg=load_config(root=root)
 assert cfg.base['solver']['mip_rel_gap']==.01 and cfg.base['solver']['time_limit_s']==600 and cfg.base['solver']['threads']==4
 assert cfg.base['solver']['highs_extra']=={} and cfg.base['solver']['tighten'] is False and cfg.base['solver']['warm_start'] is False and cfg.base['solver']['commit_step_periods']==1
 case=prepare(cfg,read_only=True,verbose=False)
 assert case.profiles.to_csv(index=False).encode()==(P/'profiles.csv').read_bytes(),'Exact original profile serialization must reproduce'
 assert case.bids==json.loads((P/'resolved_bids.json').read_text())
 return cfg,case
if a.mode=='prepare':
 O.mkdir(exist_ok=False,parents=True);shutil.copytree(P/'scenario_snapshot',O/'scenario_snapshot')
 cfg,case=cfg_case(O/'scenario_snapshot');m=scuc.build_model(case);m.write(str(O/'original_model.lp'),io_options={'symbolic_solver_labels':True});counts=model_counts(m)
 cfg.base['solver']['highs_extra']={'parallel':'on'};cfg.base['solver']['log']=True
 candidate=scuc.build_model(case);candidate.write(str(O/'parallel_model.lp'),io_options={'symbolic_solver_labels':True})
 assert digest(O/'original_model.lp')==digest(O/'parallel_model.lp'),'Strategy must not alter model'
 h=highspy.Highs();queries={}
 for key,value in [('parallel','on'),('threads',4),('mip_rel_gap',.01),('time_limit',600.)]:
  before=h.getOptionValue(key);assert before[0]==highspy.HighsStatus.kOk;assert h.setOptionValue(key,value)==highspy.HighsStatus.kOk
  queries[key]={'default':str(before[1]) if isinstance(before[1],float) and not np.isfinite(before[1]) else before[1],'requested':value,'readback':h.getOptionValue(key)[1]}
 atomic_json(O/'strategy_spec.json',dict(source_archive=str(P),source_code=str(ROOT),source_code_commit=source_commit(),baseline_core_commit='50223f4',scope='DA only; no extra reserve, no physical/economic/market changes; no previous solution supplied',runtime_solver=cfg.base['solver'],strategy_changes={'parallel':'on'},reporting_changes={'log':True},input_hashes=input_hashes(O/'scenario_snapshot'),source_input_hashes=input_hashes(P/'scenario_snapshot'),profiles_source_sha256=digest(P/'profiles.csv'),resolved_bids_source_sha256=digest(P/'resolved_bids.json'),exact_original_profile_csv_reproduced=True,model_counts=counts,original_model_sha256=digest(O/'original_model.lp'),candidate_model_sha256=digest(O/'parallel_model.lp'),identical_model_lp_bytes=True,highs_version=highspy.Highs().version(),option_verification=queries,runtime_load_note='Prepared only. Record concurrent CPU-heavy workloads before running.'))
 atomic_json(O/'trial_status.json',{'status':'prepared_not_run'});print(json.dumps({'status':'prepared_not_run','model_counts':counts,'model_sha256':digest(O/'original_model.lp'),'option_verification':queries},indent=2))
else:
 status=json.loads((O/'trial_status.json').read_text());assert status['status']=='prepared_not_run','No blind repeat of an active/completed/failed trial'
 assert a.runtime_load_note.strip(),'Explicit concurrent CPU workload record required'
 spec=json.loads((O/'strategy_spec.json').read_text());assert input_hashes(O/'scenario_snapshot')==spec['input_hashes']==input_hashes(P/'scenario_snapshot')
 cfg,case=cfg_case(O/'scenario_snapshot');cfg.base['solver']['highs_extra']={'parallel':'on'};cfg.base['solver']['log']=True
 start=time.perf_counter();atomic_json(O/'trial_status.json',{'status':'running','runtime_load_note':a.runtime_load_note,'requested_solver':cfg.base['solver']});capture_provenance(O,cfg.root,source_root=ROOT)
 try:
  def event(e):atomic_json(O/'stage_diagnostics.json',e['stage_diagnostics'])
  res=run_da(case,verbose=True,stage_callback=event);write_outputs(res,O/'da',make_plots=False)
  atomic_json(O/'result.json',dict(scuc=res.scuc_info,sced=res.sced_info,slack=res.slack,stage_comparison=res.stage_comparison,model_counts_before_integer_fixing=spec['model_counts'],model_counts_after_sced=model_counts(res.model),runtime_load_note=a.runtime_load_note,elapsed_s=time.perf_counter()-start,input_hashes_unchanged=input_hashes(O/'scenario_snapshot')==spec['input_hashes'],requested_gap_met=bool(res.scuc_info['gap']<=.01+1e-9),physical_feasibility_scope='DA model only; no RT resimulation; preserved original physical/economic constraints'))
  atomic_json(O/'trial_status.json',{'status':'completed','requested_gap_met':bool(res.scuc_info['gap']<=.01+1e-9),'elapsed_s':time.perf_counter()-start})
  print('TRIAL COMPLETE',json.dumps(res.scuc_info),flush=True)
 except Exception as e:
  atomic_json(O/'trial_status.json',{'status':'failed','error_type':type(e).__name__,'error':str(e),'elapsed_s':time.perf_counter()-start});traceback.print_exc();raise
