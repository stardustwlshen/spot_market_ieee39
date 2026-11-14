"""Accepted new SCUC → synthetic hourly merit selections → fixed SCED boundaries.
No dispatch, real qualification declaration, service payment, or optimizer.
"""
from prepare import *
from datetime import datetime,timedelta
sys.path.insert(0,str(ROOT/'guangdong_r31_rule_agc_20261112'))
from r31_agc import clear_hour

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--review',type=Path,required=True);a=ap.parse_args()
 report=read(a.review/'POST_MIP_REVIEW.json');need(report['accepted']is True and report['SCUC_accepted']is True and report['native_status']=='Optimal'and report['gap']<=.001,'accepted actual new SCUC')
 xp=a.review/'full_lifted_primal.json';need(sha(xp)==report['full_lifted_primal_sha256'],'actual full lift pin')
 sp=a.review/'fixed_integer_spectrum.json';need(sha(sp)==report['fixed_integer_spectrum_sha256'],'same selected spectrum')
 decl=read(BASE/'SYNTHETIC_AGC_DECLARATION.json');ready=read(BASE/'READY.json');need(sha(BASE/'SYNTHETIC_AGC_DECLARATION.json')==ready['pins'][str(BASE/'SYNTHETIC_AGC_DECLARATION.json')],'same synthetic declarations')
 n=read(BASE/'prepared_v1/full_names.json');x=read(xp);ix={name.replace("'",''):i for i,name in enumerate(n['variables'])};need(len(x)==len(ix),'complete unique primal')
 ip=ROOT/'guangdong_after_reset_r23_native_20261112/native_once_v1/ieee39_separate_prepared_input.json';case=loads(ip.read_bytes());G=list(case['energy_inputs']['thermal']);day=datetime.fromisoformat(case['contract']['day_start'])
 fp=a.review/'sced_prepared_v1';fm=read(fp/'PREPARATION.json')
 need(fm['SCUC_accepted']is True and fm['SCUC_review_sha256']==sha(a.review/'POST_MIP_REVIEW.json'),'same accepted SCUC fixed SCED')
 for f,h in fm['artifact_hashes'].items():need(sha(fp/f)==h,'fixed SCED artifact')
 fixed=read(fp/'fixed_full_payload.json');fn=read(fp/'full_names.json');ri={name:i for i,name in enumerate(fn['constraints'])};fvi={name.replace("'",''):i for i,name in enumerate(fn['variables'])};hourly=[]
 for hour in range(24):
  units=[]
  for u in decl['units']:
   g=u['resource_id'];stable=all(abs(x[ix[f'stable[{g},{t}]']]-1)<=1e-6 and abs(x[ix[f'off[{g},{t}]']])<=1e-6 for t in range(12*hour,12*(hour+1)))
   need(stable,'whole-hour actual stable selection required')
   units.append(dict(u,stable_online=stable))
  result=clear_hour(units,demand_mw=decl['zone_demand_equals_required_supply_mw'],local_floor_mw=decl['local_floor_mw'],full_control_area_kmax=decl['full_control_area_kmax'],as_of=case['contract']['publication_at'],hour_start=(day+timedelta(hours=hour)).isoformat())
  need(result==decl['D_hours'][hour],'actual-state selection differs from prearranged synthetic declaration')
  need(result['unmet_mw']==0 and set(result['awards_mw'])==set(G) and set(result['awards_mw'].values())=={10.},'50MW only declared five providers')
  margins=[]
  for t in range(hour*12,(hour+1)*12):
   for gi,g in enumerate(G):
    amount=result['awards_mw'][g];p=fvi[f'power[{g},{t}]'];minp=case['energy_inputs']['thermal'][g]['min_mw'][t]+amount;maxp=case['energy_inputs']['thermal'][g]['max_mw'][t]-amount
    lo=fixed['rows'][ri[f'free_lower[{g},{t}]']];hi=fixed['rows'][ri[f'free_upper[{g},{t}]']]
    need(dict(lo['terms'])=={p:-1.}and lo['ub']==-minp and dict(hi['terms'])=={p:1.}and hi['ub']==maxp,'actual selected AGC fixed SCED power boundaries')
    for off,var in((0,'primary'),(1,'contingency')):
     row=fixed['rows'][ri[f'reserves[{24*t+3+gi*4+off}]']];rv=fvi[f'{var}[{g},{t}]'];need(dict(row['terms'])=={p:1.,rv:1.}and row['ub']==maxp,'selected AGC/shared reserve capacity mismatch')
     margins.append(maxp-x[p]-x[rv])
   need(fixed['rows'][ri[f'reserves[{24*t+24}]']]['lb']==400.,'accident400')
  need(min(margins)>=-1e-6,'actual AGC/shared reserve headroom')
  hourly.append(dict(hour=hour,selection=result,actual_whole_hour_stable=G,minimum_shared_reserve_margin_mw=min(margins),fixed_SCED_boundaries_equal_selected_symmetric_awards=True))
 output=dict(schema='gd.research.r42.actual_SCUC.synthetic_AGC_to_SCED.v1',status='pass_24_hours_5x10MW',hours=hourly,actual_SCUC_review_sha256=sha(a.review/'POST_MIP_REVIEW.json'),actual_full_lifted_primal_sha256=sha(xp),same_fixed_spectrum_sha256=sha(sp),declaration_sha256=sha(BASE/'SYNTHETIC_AGC_DECLARATION.json'),fixed_SCED_preparation_sha256=sha(fp/'PREPARATION.json'),source_R31_sha256=sha(ROOT/'guangdong_r31_rule_agc_20261112/r31_agc.py'),solver_calls=0,actual_qualifications_verified=False,formal_publication_allowed=False,actual_mileage=None,formal_bill=None,payable=False,execution_authority=False,meaning='SCUC was conditional on synthetic AGC prearrangement; actual whole-hour states now validate merit selections and their exact SCED reservation. Not an uncoupled sequential optimum.')
 write(a.review/'ACTUAL_AGC_SELECTION_TO_SCED.json',output);print('24 hours each5x10MW; marginal price14 research units; same new fixed SCED bounds; no native')
if __name__=='__main__':main()
