from pathlib import Path
import sys, os
import gd_live_control as ctl
B=Path(__file__).resolve().parent
sys.path.insert(0,str(B/'gd_live_market_runtime'))
from session import Pipeline,plan,RollingHost
if __name__=='__main__':
 ctl.sources()
 if sys.argv[1]in ('validate','stops','declare_nextday'):
  from source import TypedSource
  from v6_runtime import V6Session
  s=V6Session(sys.argv[2],research_fixed_curve_events=True);ctl.need(str(Path(s.context['selected_DA']).resolve())==str(Path(sys.argv[3]).resolve()),'DA/run mismatch')
  import json
  from stop_choices import inspect,validate_request
  if sys.argv[1]=='declare_nextday':
   from day_selection import create
   ctl.need(sys.argv[6]=='--confirmed-research','explicit declaration permission');print(json.dumps(create(s,sys.argv[4],sys.argv[5],confirmed=True)));sys.exit(0)
  windows=(int(sys.argv[4])if len(sys.argv)>4 else 4)if sys.argv[1]=='stops'else int(sys.argv[9]);inventory=inspect(s,windows=windows)
  if sys.argv[1]=='stops':print(json.dumps(inventory));sys.exit(0)
  request=validate_request(inventory,json.loads(sys.argv[8]));selection=json.loads(sys.argv[10]);explicit=bool(int(sys.argv[11]));checked={};revision=bool(int(sys.argv[12]))if len(sys.argv)>12 else False
  post_enabled=bool(int(sys.argv[13]))if len(sys.argv)>13 else False;post_paths=json.loads(sys.argv[14])if len(sys.argv)>14 else None
  post_day=bool(int(sys.argv[15]))if len(sys.argv)>15 else False
  from post_selection import preflight as post_preflight
  post_checked=post_preflight(s,enabled=post_enabled,declarations=post_paths,windows=windows,reference_revision=revision)
  ctl.need(not(selection or explicit or revision or post_day),'single-day simulation does not consume nextday declarations or extend PRICE beyond24:00')
  checked=dict(single_day_scope='truncate_at_24h',nextday_required=False,extra_native_calls=0)
  from post_day_selection import check as day_check
  post_day_checked=day_check(s,checked,enabled=post_day,windows=windows,reference_revision=revision,post_scuc_planning=post_enabled,post_declarations=post_paths)
  loss_once=bool(int(sys.argv[16]))if len(sys.argv)>16 else False
  from loss_option import preflight
  loss_checked=preflight(s,enabled=loss_once,ac_once=bool(int(sys.argv[6])))
  finite_h1=bool(int(sys.argv[17]))if len(sys.argv)>17 else False
  finite_hourly=bool(int(sys.argv[18]))if len(sys.argv)>18 else False
  from finite_option import preflight as finite_preflight
  finite_checked=finite_preflight(s,enabled=finite_h1,hourly=finite_hourly,windows=windows,post_scuc_planning=post_enabled,post_scuc_day=post_day,reference_revision=revision,candidate_loss_once=loss_once)
  z=plan(finite_h1=finite_h1,finite_hourly=finite_hourly,candidate_loss_once=loss_once,windows=windows,reference_revision=revision,post_scuc_planning=post_enabled,post_scuc_day=post_day,research_event_simulation=bool(int(sys.argv[4])),counterfactual_continue=bool(int(sys.argv[5])),ac_once=bool(int(sys.argv[6])),event_price_profile=bool(int(sys.argv[7])))
  z.update(finite_preflight=finite_checked,loss_preflight=loss_checked,post_day_preflight=post_day_checked,post_preflight=post_checked,post_declarations=post_checked['declarations']if post_checked else None,stop_request=request,stop_inventory=inventory,nextday_selection=selection,nextday_explicit=explicit,nextday_preflight=checked);print(json.dumps(z));sys.exit(0)
 ctl.need(sys.argv[1]=='run','internal run or validate only');out=Path(sys.argv[2])
 if os.name=='nt':
  from gd_process_guard import claim_windows_worker
  guard=claim_windows_worker(out)
 p=ctl.read(out/'PLAN.json')
 try:
  from gd_dispatch_mode import supported
  from gd_live_market_runtime.pricing_delta import policy
  ctl.need(p.get('pricing_delta_policy')==policy(p.get('pricing_delta',.1)),'explicit matching research delta plan required')
  supported(p.get('dispatch_mode','provincial8'))
  ctl.need(p.get('model_scope')=='active_power_dc' and p.get('ac_assessment')=='not_in_model_scope' and p.get('reactive_power_modeled') is False,'explicit current DC plan required; historical plans cannot be relabeled')
  for f,h in p['source_pins'].items():ctl.need(ctl.sha(f)==h,'planned source changed')
  x=Pipeline(p['run'],out/'pipeline',research=True,research_event_simulation=p['research_event_simulation'],counterfactual_continue=p['counterfactual_continue'],ac_once=p['ac_once'],event_price_profile=p['event_price_profile'],stop_request=p.get('stop_request'),windows=p.get('windows',4),nextday_selection=p.get('nextday_selection'),nextday_explicit=p.get('nextday_explicit',False),reference_revision=p.get('reference_revision',False),post_scuc_planning=p.get('post_scuc_planning',False),post_declarations=p.get('post_declarations'),post_scuc_day=p.get('post_scuc_day',False),candidate_loss_once=p.get('candidate_loss_once',False),finite_h1=p.get('finite_h1',False),finite_hourly=p.get('finite_hourly',False),pricing_clock_path=p.get('pricing_clock_path'),pricing_delta=p['pricing_delta'],durable_checkpoint=p.get('durable_checkpoint'));ctl.need(str(Path(x.host.session.context['selected_DA']).resolve())==p['da'],'wrong DA');z=x.run(execute_native=True);result=dict(status='bounded_checkpoint_reached' if z.get('status')=='bounded_checkpoint_reached' else 'complete_research',hour=z)
 except Exception as e:
  result=dict(status='blocked_or_unknown_no_retry',error=str(e),no_automatic_fallback=True)
  if 'x'in locals():
   try:
    events=x.host.read().get('event_workflow',{});events.update(pipeline_blocked=True,pipeline_block_reason=str(e),publication_allowed=False)
    f=out/'pipeline/EVENTS.blocked.json';ctl.write(f,events)
   except Exception as state_error:result['event_read_error']=str(state_error)
 ctl.write(out/'EXIT.json',dict(**result,model_scope='active_power_dc',ac_assessment='not_in_model_scope',reactive_power_modeled=False,publication_allowed=False,safety_release=False))
