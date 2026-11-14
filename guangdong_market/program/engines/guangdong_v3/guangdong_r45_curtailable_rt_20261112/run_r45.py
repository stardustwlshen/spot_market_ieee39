"""Explicit prepare OR execute new 4/96-window local research chain. Never grid dispatch."""
import argparse,json,sys
from pathlib import Path
sys.dont_write_bytecode=True

def main():
    p=argparse.ArgumentParser();p.add_argument('action',choices=['prepare','run'])
    p.add_argument('--source-config',type=Path,required=True);p.add_argument('--run-plan',type=Path,required=True)
    p.add_argument('--run-plan-sha256');p.add_argument('--output',type=Path)
    p.add_argument('--windows',type=int,choices=[4],default=4);a=p.parse_args()
    from r45smm import gd_r45_priced_native_adapter as api
    from r45smm import gd_r45_source_contract as source
    from r45smm.gd_r15_da_rt_bridge import digest,file_sha,canonical
    if a.action=='prepare':
        plan,permit=source.load_contract(a.source_config,preparation_root=None,policy=source.POLICY)
        # Preparing does not create a worker or consume a solver call.
        release=dict(schema=api.SCHEMA+'.run_plan',approved=True,simulation_runner_authorization=True,execution_authority=False,
                     start_index=44,window_count=a.windows,max_native_calls=2*a.windows,solver_options=api.OPTIONS,
                     price_missing_reasons=['confirmed_PRICE_Infeasible','research_terminal_domain_unavailable'],
                     policy_sha256=api.POLICY_SHA256,window_policy_sha256=api.WINDOW_POLICY_SHA256,
                     price_terminal_policy_sha256=api.TERMINAL_POLICY_SHA256,formal_strict=False,retry_allowed=False,
                     source_tree_sha256=api.source_manifest()['tree_sha256'],runtime_identity=api.runtime_identity(),
                     serialized_evidence_estimate_sha256=file_sha(api.ESTIMATE_PATH),contract_sha256=digest(plan),seed_sha256=digest(plan['seed_proof']),
                     source_config_sha256=file_sha(a.source_config),meaning='local research solver plan only, no real grid authority')
        with a.run_plan.open('xb')as f:f.write(canonical(release))
        print(json.dumps({'prepared_run_plan_sha256':file_sha(a.run_plan),'native_calls':0,'windows':a.windows}));return
    if not a.output or not a.run_plan_sha256:p.error('run needs --output and --run-plan-sha256')
    plan=json.loads(a.run_plan.read_text())
    if plan['source_config_sha256']!=file_sha(a.source_config):raise ValueError('source config changed')
    run=api.create_priced_run(r14_core=a.source_config,preparation_root=None,output=a.output,release_path=a.run_plan,release_sha256=a.run_plan_sha256)
    try:
        for i in range(plan['start_index'],plan['start_index']+plan['window_count']):
            accepted=api.run_priced_window(run,i);api.advance_to(run,accepted['window']['start'])
            print(json.dumps(api.compact_report(run)),flush=True)
        api.advance_to(run,accepted['window']['delivery_end']);api.verify_post_research(run)
        result=api.report(run);result['simulation_runner_authorization']=True;result['execution_authority']=False
        api._write(a.output,'result.json',result)
    except BaseException:
        try:api._write(a.output,'failed_session_report.json',api.report(run))
        except Exception:pass
        raise
if __name__=='__main__':main()
