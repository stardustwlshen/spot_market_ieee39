from pathlib import Path
import json,hashlib,sys
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parent))
import run_matrix_profile as m

def fixture(tmp_path,coefficient=2):
 r=tmp_path;r.mkdir(exist_ok=True);s=r/'da_scuc';s.mkdir()
 q=dict(variables=[dict(kind='I',lb=0,ub=2,start=None)],rows=[dict(lb=1,ub=None,terms=[[0,1.]])],objective=dict(minimize=True,constant=0.,terms=[[0,coefficient]]),dual=False,warmstart=False,preparation_elapsed_s=0,options=dict(time_limit=10.,threads=1,presolve='choose',mip_rel_gap=.001,mip_abs_gap=0.,output_flag=False))
 values=dict(template=q,names=dict(variables=['x'],constraints=['row']),network_plan=dict(network_affine=dict(injection_indices=[],box_upper_by_cell=[]),kept_original_compact_columns=[0]))
 for n,z in values.items():(s/(n+'.json')).write_text(json.dumps(z))
 run=dict(run_id='test-'+str(coefficient),input_identity='input-'+str(coefficient),source_identity='source')
 (r/'RUN.json').write_text(json.dumps(run))
 (s/'PREPARED.json').write_text(json.dumps(dict(run,stage='da_scuc',artifacts={n:dict(path='da_scuc/'+n+'.json',sha256=m.sha(s/(n+'.json')))for n in values})))
 return r

def test_two_edited_objectives_import_exactly(tmp_path):
 a=fixture(tmp_path/'a',2);b=fixture(tmp_path/'b',7)
 aa=m.activate(a);bb=m.activate(b)
 assert aa['worker_sha256']!=bb['worker_sha256']
 assert aa['optimization_calls']==bb['optimization_calls']==0
 assert m.worker(a).exists()and m.worker(b).exists()
 assert m.activate(a)==aa # verified reuse without model import or optimization
 z=m.read(b/'matrix_profile/ACTUAL_STORAGE_IMPORT_VERIFICATION.json');assert z['model_import_only']and z['all_stored_coefficients_indices_bounds_cost_types_rows_exact_checked']

def test_identity_and_hash_reject(tmp_path):
 r=fixture(tmp_path);m.activate(r)
 p=r/'RUN.json';z=m.read(p);z['run_id']='foreign';p.write_text(json.dumps(z))
 with pytest.raises(ValueError,match='another run'):m.verify(r)
 z['run_id']='test-2';p.write_text(json.dumps(z));(r/'da_scuc/names.json').write_text('{}')
 with pytest.raises(ValueError,match='changed'):m.verify(r)

def test_partial_profile_not_retried(tmp_path):
 r=fixture(tmp_path);(r/'matrix_profile').mkdir()
 with pytest.raises(ValueError,match='partial'):m.activate(r)
