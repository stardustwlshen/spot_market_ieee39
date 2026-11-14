"""Pure new scenario preparation; parent alone may launch native optimizer."""
from pathlib import Path
import sys,json,hashlib,argparse
from fractions import Fraction as F
ROOT=Path(__file__).resolve().parent.parent;BASE=Path(__file__).resolve().parent
sys.path[:0]=[str(ROOT/'guangdong_after_reset_r23_compressed_20261112/repo/src'),str(ROOT/'guangdong_r25_strong_scuc_20261112')]
from smm.gd_october_job_codec import loads
from smm.gd_r23_equivalent_compression import compress,canonical,guards as phase_guards
from strong_ramp import guards,build_cuts
from r42_model import build,need
from network_elimination import derive,transform

def read(p):return json.loads(Path(p).read_bytes())
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def write(p,z):p.write_bytes(canonical(z))
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--output',type=Path,required=True);ap.add_argument('--penalty',type=float,required=True);a=ap.parse_args()
 op=ROOT/'guangdong_after_reset_r23_native_20261112/native_once_v1';qp=op/'ieee39_separate_prepared_scuc/request.json';np=qp.with_name('names.json');ip=op/'ieee39_separate_prepared_input.json'
 for p,h in [(qp,'becec89056ac871930b38ae210101459049e99dff0c308db9c6b92397589ccff'),(np,'4f0f17190d6f690881a93e0f2e00504036037abb8678428c6212147f433a2ad9'),(ip,'c10e5cbfbe0f39f028d10cb2b2d30003e803033a3d883f4c753339bc9c1cdbe9')]:need(sha(p)==h,'original source fixed')
 old=read(qp);on=read(np);case=loads(ip.read_bytes());full,n,delta=build(old,on,case,curtailment_penalty=a.penalty,penalty_basis='explicit_research_parameter')
 print('new full built',flush=True)
 # Existing thermal phase and ramp proof unchanged; new restrictions can only narrow its domain.
 pg=phase_guards(old,on,{**case,'batteries':{}});cutproof=guards(case,old,on,pg)
 compact,cn,cplan=compress(full,n,case);cuts,labels=build_cuts(case,old,on,cplan);compact['rows']+=cuts;cn['constraints']+=labels
 print('compressed',len(compact['variables']),len(compact['rows']),flush=True)
 affine=derive(compact,cn,case);reduced,rn,nplan=transform(compact,cn,affine)
 reduced['options'].update(mip_heuristic_effort=0.,mip_heuristic_run_rens=False,mip_heuristic_run_rins=False,mip_heuristic_run_root_reduced_cost=False)
 # No old primal is feasible/claimed: no warm start or accepted source result reused.
 reduced['warmstart']=False
 a.output.mkdir(exist_ok=False)
 for f,x in [('full_template.json',full),('full_names.json',n),('compact_template.json',compact),('compact_names.json',cn),('compression_plan.json',cplan),('added_ramp_cuts.json',{'rows':cuts,'names':labels,'original_guard':cutproof}),('template.json',reduced),('names.json',rn),('network_elimination_plan.json',nplan),('SCENARIO_DELTA.json',delta)]:write(a.output/f,x)
 proof=read(ROOT/'guangdong_r39_all_day_agc_necessary_proof_20261112/EXACT_PROOFS.json')['proofs'][0];B=F(proof['aggregate_SOC_increase_lower']['fraction']);b=F(proof['SOC_coefficients']['discharge']['fraction']);minimum=(B-1600)/(12*b)
 sensitivity=dict(scope='pure objective coefficients / necessary penalty-cost floor, NOT solved optima',curtailment_energy_necessary_lower_mwh=float(minimum),exact_lower=str(minimum),interval=[89,169],scenarios=[dict(research_penalty_yuan_mwh=v,objective_cost_coefficient_per_cell=v/12,necessary_interval_penalty_lower_yuan=float(minimum*v))for v in (0.,100.,1000.,10000.)],actual_curtailment_not_fixed=True)
 write(a.output/'PURE_OBJECTIVE_SENSITIVITY.json',sensitivity)
 meta=dict(schema='gd.research.r42.curtailable_D_AGC.preparation.v1',native_calls=0,columns=len(reduced['variables']),rows=len(reduced['rows']),free_integer_columns=sum(v['kind']!='C'and v['lb']!=v['ub']for v in reduced['variables']),all_day_AGC_mw=50,AGC_scope_physical_cells=[0,288],noquote_zero_bid_proxy=True,curtailment_penalty_yuan_mwh=a.penalty,penalty_is_research_not_official=True,original_case_modified=False,not_equivalent_to_old_fixed_renewable_case=True,qualification_and_plant_data_synthetic=True,SCUC_accepted=False,formal_publication_allowed=False,research_publication_allowed=False,execution_authority=False,worker_time_limit_s=600,solver='HiGHS1.15.1',threads=1,mip_rel_gap=.001,mip_abs_gap=0.,future_review_requires_new_full_matrix_not_old_fixed_case=True,network_inverse_guard=affine['guard'],source_pins={str(p):sha(p)for p in [qp,np,ip,BASE/'r42_model.py',BASE/'network_elimination.py',BASE/'prepare.py',ROOT/'guangdong_r25_strong_scuc_20261112/strong_ramp.py']},artifact_hashes={p.name:sha(p)for p in a.output.iterdir()})
 write(a.output/'PREPARATION.json',meta);print(json.dumps({k:meta[k]for k in ('columns','rows','free_integer_columns','network_inverse_guard')}));print('preparation_sha256',sha(a.output/'PREPARATION.json'))
if __name__=='__main__':main()
