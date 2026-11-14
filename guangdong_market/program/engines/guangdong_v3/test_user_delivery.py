from pathlib import Path
import importlib.util,sys,json,subprocess,hashlib
import pytest
B=Path(__file__).resolve().parent

def mod(name,path):
 spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

@pytest.mark.parametrize('name',['rt_precision','price_standard'])
def test_safe_profiles(name,tmp_path):
 m=mod(name,B/'safe_workers'/f'{name}.py')
 for status in ('Infeasible','Unbounded','Time limit reached','Optimal'):
  out=tmp_path/status;m.write_complete(out,dict(native_status=status,status=status.lower(),has_primal=False,diagnostic=float('inf')))
  z=json.loads(out.read_text());assert z['native_status']==status and z['diagnostic']is None and z['nonfinite_fields'][0]['symbol']=='+Infinity'
  with pytest.raises(FileExistsError):m.write_complete(out,{})
 assert m.safe_response(dict(primal=[float('nan')],has_primal=True))['has_primal']is False

def test_actual_price_launch_mock_infeasible(tmp_path,monkeypatch):
 sys.path.insert(0,str(B/'guangdong_pipeline_20261112'));import pipeline_rt_price_native as m
 p=tmp_path/'W0/prepared';p.mkdir(parents=True)
 q=dict(dual=True,warmstart=False,variables=[dict(kind='C')],options=dict(threads=1,time_limit=60,mip_rel_gap=.001,mip_abs_gap=0))
 (p/'template.json').write_text(json.dumps(q));(p/'PREPARATION.json').write_text('{}')
 monkeypatch.setattr(m,'verify_prepared',lambda p:dict(window=0,receipt=dict(run_id='test-newrun')))
 class Process:
  pid=987;returncode=0
  def __init__(self,args,**kw):
   assert Path(args[2])==m.WORKER
   w=mod('test_standard',m.WORKER);w.write_complete(args[4],dict(native_status='Infeasible',status='infeasible',has_primal=False,dual_valid=False,diagnostic=float('inf')))
  def poll(self):return 0
 sys.path.insert(0,str(B/'guangdong_after_reset_r23_compressed_20261112/repo/src'));from smm import highs_solver
 monkeypatch.setattr(m.subprocess,'Popen',Process)
 def review(p,n):
  assert json.loads((n/'TRANSPORT_RECEIPT.json').read_text())['mathematical_acceptance']is False
  raise ValueError('nonOptimal PRICE pending')
 monkeypatch.setattr(m,'review_window',review)
 with pytest.raises(ValueError,match='pending'):m.execute_window(p,execute_native=True)
 assert json.loads((p.parent/'native/response.json').read_text())['diagnostic']is None

def test_foreign_cwd_and_no_native(tmp_path):
 c=subprocess.run([sys.executable,'-B',str(B/'market.py'),'energy','run','--run',str(tmp_path/'fresh'),'--rt-windows','4'],cwd=tmp_path,capture_output=True,text=True)
 assert c.returncode==0 and 'dryrun_no_native_no_initialization'in c.stdout and not(tmp_path/'fresh').exists()

def test_status_missing_ac(tmp_path):
 m=mod('status_test',B/'result_status.py');(tmp_path/'RUN.json').write_text(json.dumps(dict(run_id='test')))
 z=m.status(tmp_path);assert z['ac']['status']=='not_checked'and z['formal_bill']is None
 n=tmp_path/'native';n.mkdir();(n/'request.json').write_text('{}');assert m.status(tmp_path)['blocking_unknown_attempt']

def test_ac_wrong_run(tmp_path):
 m=mod('status_wrong',B/'result_status.py');(tmp_path/'RUN.json').write_text('{"run_id":"new"}')
 p=tmp_path/'plan';p.write_text(json.dumps(dict(schema='gd.pipeline.fresh_executed_to_AC.v1',run_id='old',run_root=str(tmp_path))))
 r=tmp_path/'result';r.write_text('{}')
 with pytest.raises(ValueError,match='another run'):m.status(tmp_path,p,r)

def test_real_historical_partial4_and_full96():
 root=B.parent/'guangdong_pipeline_20261112/pure_fresh_run_v1'
 if not root.exists():pytest.skip('external actual evidence not bundled')
 m=mod('actual_partial_status',B/'result_status.py')
 part=m.status(root,report_path=root/'rt/PARTIAL_W4.json')
 assert part['energy']['rt_executed_cells']==9 and part['energy']['price_windows']==4
 assert part['energy']['status']=='pending'and part['energy']['settlement_rows']==0
 full=m.status(root)
 assert full['energy']['rt_executed_cells']==288 and full['energy']['price_windows']==96
 assert full['energy']['status']=='research_energy_chain_complete'
