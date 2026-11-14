"""One bounded live-host interaction. Disk messages are requests, never source authority."""
from pathlib import Path
from datetime import datetime,timedelta
import json,time,secrets,hashlib
from safety_reclear_control import SafetyReclearControl

def read(p):return json.loads(Path(p).read_bytes())
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def need(x,m):
 if not x:raise ValueError(m)
def put(p,z):
 t=p.with_suffix('.next');t.write_text(json.dumps(z,indent=2,allow_nan=False));t.replace(p)

def live_description(host,w):
 prepared,actual=host.accepted_source(w);prepared=Path(prepared)
 case=host.session.program.engine.loads((prepared/'derived_case.json').read_bytes())
 cutoff=datetime.fromisoformat(read(host.cp)['cutoff']);begin=cutoff+timedelta(minutes=15)
 source_start=datetime.fromisoformat(read(prepared/'SUMMARY.json')['domain_start'])
 index=int((begin-source_start).total_seconds()/300)
 units={g:{'minimum_cap_mw':q['min_mw'][index]+10,'maximum_cap_mw':q['max_mw'][index]-10}for g,q in case['energy_inputs']['thermal'].items()}
 return dict(branch_id=host.branch_id,window=host.window,base_source_window=w,seed_case_sha256=host.agc.seed_sha,base_SCED_sha256=sha(Path(actual)/'SCED/response.json.gz'),declared_at=cutoff.isoformat(),start=begin.isoformat(),end=(begin+timedelta(minutes=5)).isoformat(),units=units)

def declaration(desc,request):
 need(set(request)=={'unit','maximum_power_mw','reason','source_statement'},'only supported cap input fields')
 need(request['unit']in desc['units'],'unknown thermal unit')
 return dict(schema='gd.research.thermal_power_cap.v1',constraint_semantics='redispatch_basepoint_only',mode='explicit_research_tightening',**{k:desc[k]for k in ('branch_id','base_source_window','seed_case_sha256','base_SCED_sha256','declared_at')},reason=request['reason'],source_statement=request['source_statement'],preserve_AGC_mw=50,preserve_positive_reserve_mw=400,preserve_accident_reserve_mw=400,constraints=[dict(unit=request['unit'],start=desc['start'],end=desc['end'],maximum_power_mw=request['maximum_power_mw'])])

def wait_for_future_candidate(pipeline,w,original_report,*,timeout_seconds=600):
 """One proposal plus separate registration/execution confirmations; never restart."""
 host=pipeline.host;folder=pipeline.output/'FUTURE_CAP';folder.mkdir();statefile=folder/'STATE.json'
 nonce=secrets.token_hex(24);expiry=time.time()+timeout_seconds
 state=dict(status='waiting_proposal',nonce=nonce,expires_at_epoch=expiry,description=live_description(host,w),original_safety=original_report,publication_allowed=False,actual_native_calls=0,actual_AC_calls=0,live_host_required=True,prepared_candidate_execution_supported=True)
 put(statefile,state);control=SafetyReclearControl(host,safety=pipeline.safety);d=None;review=None
 try:
  control.inspect_trigger(w)
  for phase in ('proposal','confirm','execute'):
   command=folder/(phase+'.json')
   while not command.exists():
    if time.time()>=expiry:raise TimeoutError('live interaction expired; no restore')
    time.sleep(.25)
   need(time.time()<expiry,'expired command');q=read(command);need(q.get('nonce')==nonce,'wrong live interaction')
   if phase=='proposal':
    need(set(q)=={'nonce','request'},'unsupported proposal');d=declaration(state['description'],q['request']);review=control.review(d)
    state.update(status='waiting_confirmation',registration_plan=review);put(statefile,state)
   elif phase=='confirm':
    need(set(q)=={'nonce','confirmation_digest'},'unsupported confirmation')
    need(q['confirmation_digest']==review['confirmation_digest'],'stale reviewed plan')
    confirmation=control.confirm_prepare(d,confirmed=True,expected_confirmation_digest=q['confirmation_digest'])
    prepared=confirmation['prepared'];budget=confirmation['plan'];receipt=dict(registered=True,declaration_path=prepared['declaration_path'])
    state.update(status='waiting_execution_confirmation',registration=receipt,prepared=prepared,budget=budget,reason='future basepoint-only candidate; not historical AC repair; separate two-call authorization required')
    put(statefile,state)
   else:
    need(set(q)=={'nonce','confirmation_digest','execute_native'}and q['execute_native']is True,'explicit two-call execution permission required')
    need(q['confirmation_digest']==review['confirmation_digest'],'wrong prepared candidate')
    state.update(status='executing_once_unknown_until_return',execution_attempted=True,actual_native_calls=None,authorized_maximum_native_calls=2);put(statefile,state)
    # Same live host, existing prepared occurrence, normal SCUC+fixed SCED acceptance.
    completed=control.execute(execute_native=True)
    future_report=completed['safety']
    state.update(status='future_pair_accepted_not_safety_released',future_safety=future_report,controller_events=completed['events'],actual_native_calls=2,original_safety=original_report,publication_allowed=False,PRICE_calls=0,AC_calls=0)
    put(statefile,state)
 except Exception as exc:
  state.update(status='blocked_or_unknown_no_retry'if state.get('execution_attempted')else'blocked_or_expired',error=str(exc));put(statefile,state)
 return state
