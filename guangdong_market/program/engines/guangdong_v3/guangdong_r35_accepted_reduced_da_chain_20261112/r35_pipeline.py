"""Data-only, provenance-bound research DA lifecycle. No native solver or live permit."""
from pathlib import Path
from hashlib import sha256
from datetime import datetime
import json,math
SCHEMA='gd.research.r35.accepted_reduced_da.v1'
def require(ok,msg):
    if not ok:raise ValueError(msg)
def canonical(x):return json.dumps(x,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
def digest(x):return sha256(canonical(x)).hexdigest()
def sha(p):return sha256(Path(p).read_bytes()).hexdigest()
def read(p):return json.loads(Path(p).read_bytes())
def finite(x):return type(x)in(int,float)and math.isfinite(x)
def near(a,b):return finite(a)and finite(b)and abs(a-b)<=1e-6+1e-8*max(abs(a),abs(b))
def pin(path,expected):
    require(sha(path)==expected,'artifact hash mismatch: '+str(path));return read(path)

def allowed_launch_attempt(identity,launch,response):
    if launch['attempt']==1:return True
    override=identity.get('worker_override',{})
    return (launch['attempt']==2 and identity.get('worker_override_sha256')=='4a3ec99ac92534a53c5298f2fe872f6e219a828b63b1bba2f0c5b5f461683531' and digest(override)==identity['worker_override_sha256'] and override.get('previous_attempt_optimization_calls')==0 and response.get('optimize_calls')==1 and identity.get('worker_sha256')==launch.get('worker_sha256')==override.get('new_worker_sha256'))

def load_event(paths):
    """paths explicitly names five same-native-occurrence artifact files."""
    require(set(paths)=={'request','response','identity','launch','confirmed'},'complete event paths required')
    p={k:Path(v)for k,v in paths.items()};d={k:read(v)for k,v in p.items()};i=d['identity'];c=d['confirmed'];l=d['launch'];r=d['response']
    require(i['request_sha256']==sha(p['request']),'wrong native request pairing')
    require(c['response_sha256']==sha(p['response']),'wrong native response pairing')
    require(allowed_launch_attempt(i,l,r) and l['pid']==c['pid']and c['returncode']==0,'launch/confirmed event mismatch; attempt2 only permits pinned zero-optimization import-stop override')
    require(r['backend']=='highs'and r['solver_version']=='1.15.1','unexpected backend/version')
    opts=d['request']['options'];require(opts['threads']==1 and opts['mip_rel_gap']==.001 and opts['mip_abs_gap']==0,'solver policy mismatch')
    require(r['native_settings']['threads']==1 and r['native_settings']['mip_rel_gap']==.001 and r['native_settings']['mip_abs_gap']==0,'actual native settings mismatch')
    return dict(schema=SCHEMA+'.event',request=d['request'],response=r,pins={k:dict(path=str(p[k]),sha256=sha(p[k]))for k in p},event_id=sha(p['request'])+':'+sha(p['response']))

def proof_bundle(files):
    """Role-separated proof artifacts, never aliases for the native request."""
    require('original_full_math_request'in files,'original full math proof required')
    allowed={'original_full_math_request','compression_plan','compression_preparation','strongcut_proof','strongcut_added_rows','source_postreview','fixed_integer_spectrum','network_elimination_plan','network_elimination_preparation','original_R25_template','global_native_template'}
    require(set(files)<=allowed,'unknown proof role')
    return {role:dict(path=str(z['path']),sha256=z['sha256'],verified_content=pin(z['path'],z['sha256']))for role,z in files.items()}

def audit_bound(event,path):
    a=read(path);rh=event['pins']['response']['sha256'];qh=event['pins']['request']['sha256']
    require(any(a.get(k)==rh for k in ('native_response_sha256','raw_response_sha256')),'audit not tied to this response')
    if 'native_request_sha256'in a:require(a['native_request_sha256']==qh,'audit not tied to this request')
    return a,dict(path=str(path),sha256=sha(path))

def assess_scuc(event,audit_path,proofs):
    a,p=audit_bound(event,audit_path);r=event['response'];require(proofs,'separate original model proof required')
    required=('network_elimination_plan','network_elimination_preparation','original_R25_template','global_native_template')
    require(all(k in proofs for k in required),'explicit reduced-to-original full global domain proof required')
    plan=proofs['network_elimination_plan']['verified_content'];meta=proofs['network_elimination_preparation']['verified_content'];template=proofs['global_native_template']['verified_content'];original=proofs['original_R25_template']['verified_content']
    require(plan['source_payload_sha256']==proofs['original_R25_template']['sha256'],'transform must bind original global R25 template')
    require(meta['artifact_hashes']['template.json']==proofs['global_native_template']['sha256']and meta['artifact_hashes']['network_elimination_plan.json']==proofs['network_elimination_plan']['sha256'],'template/transform preparation binding')
    require(a.get('network_elimination_plan_sha256')==proofs['network_elimination_plan']['sha256'],'new full-original audit must bind transform')
    strip=lambda q:{k:v for k,v in q.items()if k!='deadline'}
    require(strip(event['request'])==template,'actual SCUC request is not this full global transformed template')
    keep=plan['kept_original_compact_columns'];require(len(keep)==len(template['variables']),'complete transform columns')
    for v,j in zip(template['variables'],keep):require({k:z for k,z in v.items()if k!='start'}=={k:z for k,z in original['variables'][j].items()if k!='start'},'no retained bound/kind changed by transform')
    require(sum(z['kind']!='C'and z['lb']!=z['ub']for z in template['variables'])==2160,'original global2160 decisions remain free')
    if 'original_v1_request_sha256'in a:require(a['original_v1_request_sha256']==proofs['original_full_math_request']['sha256'],'original math proof differs from review')
    cert=a.get('full_original_certificate');require(isinstance(cert,dict),'full-original primal/integer certificate required')
    feasible=(r.get('has_primal')is True and finite(cert.get('max_primal_violation'))and 0<=cert['max_primal_violation']<=1e-6 and finite(cert.get('max_integer_violation'))and 0<=cert['max_integer_violation']<=1e-6 and near(cert.get('objective'),r.get('objective')))
    gap=None
    if feasible and finite(r.get('bound')):
        obj=r['objective'];bound=r['bound'];require(bound<=obj+1e-6+abs(obj)*1e-8,'invalid minimization bound')
        gap=max(0.,obj-bound)/max(abs(obj),1e-10)
        require(near(gap,r.get('native_mip_gap')),'reported/calculated MIP gap mismatch')
    accepted=feasible and gap is not None and gap<=.001 and r.get('status')=='optimal'and r.get('native_status')=='Optimal'
    return dict(stage='SCUC',event_id=event['event_id'],event_pins=event['pins'],audit=p,proof_pins={k:{t:v[t]for t in ('path','sha256')}for k,v in proofs.items()},accepted=accepted,feasible=feasible,gap=gap,native_status=r.get('native_status'),basis='actual primal/integrality/objective and native bound; no caller acceptance flag',official=False)

def assess_lp(event,audit_path,stage):
    require(stage in ('SCED','PRICE'),'LP stage');a,p=audit_bound(event,audit_path);r=event['response'];require(r.get('status')=='optimal'and r.get('native_status')=='Optimal'and r.get('dual_valid')is True and r.get('has_primal')is True,'actual Optimal LP with primal/dual required')
    cs=[a[k]for k in ('KKT','compact_KKT','expanded_full_KKT')if k in a];require(cs,'actual KKT metrics required')
    for c in cs:
        require(all(finite(c.get(k))and 0<=c[k]<=1e-6 for k in ('max_primal_violation','max_dual_sign_violation','max_complementarity')),'LP KKT residual failed')
        require(finite(c.get('absolute_gap'))and c['absolute_gap']<=1e-4 and near(c.get('primal_objective'),r.get('objective')),'LP objective/KKT gap failed')
    return dict(stage=stage,event_id=event['event_id'],event_pins=event['pins'],audit=p,accepted=True,official=False)

def source_receipt(case,report,values,scuc,sced,*,logical_available_at,generated_at):
    """Suitable for unchanged pure R24 model constructor; gates are derived above."""
    from r24_bridge import SCHEMA as R24,case_digest,digest as d24
    require(scuc['stage']=='SCUC'and sced['stage']=='SCED'and sced['accepted']and scuc['feasible'],'verified SCUC incumbent and SCED needed')
    require(scuc['event_id']!=sced['event_id'],'distinct native stages required')
    return dict(schema=R24+'.source_receipt',stage='fixed_SCED',status='Optimal',run_id=sced['event_id'],scuc_run_id=scuc['event_id'],case_sha256=case_digest(case),physical_report_sha256=d24(report),expanded_values_sha256=d24(values),source_kind='native_highs_sced',logical_available_at=logical_available_at,generated_at=generated_at,SCUC_accepted=scuc['accepted'],diagnostic_only=not scuc['accepted'],publication_allowed=False,new_fixed_sced_primal_KKT_verified=True,SCUC_fixed_discrete_match_verified=False,SCUC_request_sha256=scuc['event_pins']['request']['sha256'],SCUC_response_sha256=scuc['event_pins']['response']['sha256'],SCED_request_sha256=sced['event_pins']['request']['sha256'],SCED_response_sha256=sced['event_pins']['response']['sha256'],expanded_witness_source_sha256=d24(values),R26_SCuc_assessment=scuc,R26_SCED_assessment=sced,gate_note='fixed spectrum must be independently bound; PRICE absent, no publication')

def verify_fixed_spectrum(scuc_values,sced_values,spectrum):
    require(spectrum and len({x['name']for x in spectrum})==len(spectrum),'complete unique fixed spectrum required')
    for x in spectrum:
        n=x['name'];require(n in scuc_values and n in sced_values and near(scuc_values[n],x['value'])and near(sced_values[n],x['value']),'fixed SCUC/SCED spectrum differs')
    return True

def _research_publication(bridge,scuc,sced,price,prices98,*,price_input_sha256,available_at):
    """No caller true grants publication: all identities/gates must match assessments."""
    from r24_bridge import digest as d24,stamp
    require(bridge['DTO_sha256']==d24(bridge['DTO']),'DTO mutated')
    d=bridge['DTO'];receipt=bridge['source_receipt']
    require(scuc['accepted']and sced['accepted']and price['accepted'],'unaccepted diagnostic chain cannot publish')
    require(receipt['scuc_run_id']==scuc['event_id']and receipt['run_id']==sced['event_id'],'source stages mismatch')
    require(len({scuc['event_id'],sced['event_id'],price['event_id']})==3,'independent native stages required')
    require(price_input_sha256==bridge['DTO_sha256'],'PRICE input not this DTO')
    require(stamp(available_at)==stamp(d['planned_publication_at'])and stamp(d['fixed_state_available_at'])<=stamp(available_at)<stamp(d['grid'][0]['start']),'causal simulated publication violation')
    require(set(prices98)==set(d['nodes'])and all(len(v)==98 and all(finite(x)for x in v)for v in prices98.values()),'complete checked98 price inventory')
    return dict(schema=SCHEMA+'.research_publication',research_publication_allowed=True,official=False,formal_publication_allowed=False,execution_authority=False,legacy_R15_permit_reused=False,available_at=available_at,clock='explicit simulated publication; not historical actual completion',source_DTO_sha256=bridge['DTO_sha256'],stage_assessments=[scuc,sced,price],rows=[dict(index=t,start=g['start'],end=g['end'],quantity_mwh={r:u['rows'][t]['sced_mw']*.25 for r,u in d['resources'].items()},prices={n:v[t]for n,v in prices98.items()})for t,g in enumerate(d['grid'][:96])],representatives_publishable=False)

def finalize_research_publication(bridge,*,scuc_paths,scuc_audit,proof_files,sced_paths,sced_audit,price_paths,price_audit,price_input_path,price_template_path,prices_path,available_at):
    """Public finalizer reloads actual events/audits; accepts no acceptance boolean."""
    se=load_event(scuc_paths);de=load_event(sced_paths);pe=load_event(price_paths)
    scuc=assess_scuc(se,scuc_audit,proof_bundle(proof_files));sced=assess_lp(de,sced_audit,'SCED');price=assess_lp(pe,price_audit,'PRICE')
    from r24_bridge import digest as d24
    require(d24(read(price_input_path))==bridge['DTO_sha256'],'native PRICE DTO input mismatch')
    template=read(price_template_path);strip=lambda x:{k:v for k,v in x.items()if k not in ('deadline','preparation_elapsed_s')}
    require(strip(template)==strip(pe['request']),'native PRICE is not supplied template')
    audit=read(price_audit)
    from r24_bridge import stamp
    require('logical_available_at'in audit and stamp(audit['logical_available_at'])<=stamp(available_at),'PRICE logical availability missing/future')
    require(audit.get('prices_sha256')==sha(prices_path),'checked price artifact mismatch')
    # The independent PRICE review must tie this template to this DTO; no hash supplied only by caller.
    require(audit.get('input_sha256')==sha(price_input_path)and audit.get('template_sha256')==sha(price_template_path),'PRICE review needs input/template binding')
    raw=read(prices_path);prices=raw.get('prices98_yuan_mwh',raw.get('prices',raw))
    return _research_publication(bridge,scuc,sced,price,prices,price_input_sha256=bridge['DTO_sha256'],available_at=available_at)


def prepare_source_receipt(case,report,values,*,scuc_paths,scuc_audit,proof_files,sced_paths,sced_audit,scuc_values_path,scuc_values_sha256,sced_values_path,sced_values_sha256,spectrum_path,spectrum_sha256,logical_available_at,generated_at):
    se=load_event(scuc_paths);de=load_event(sced_paths);proofs=proof_bundle(proof_files)
    scuc=assess_scuc(se,scuc_audit,proofs);sced=assess_lp(de,sced_audit,'SCED')
    # New independent source audit binds the supplied normalized expanded maps and entire fixed spectrum.
    a=read(sced_audit)
    require(a.get('physical_report_content_sha256')==digest(report),'physical view not bound to new SCED review')
    for k,h in [('SCUC_expanded_values_sha256',scuc_values_sha256),('SCED_expanded_values_sha256',sced_values_sha256),('fixed_spectrum_sha256',spectrum_sha256)]:require(a.get(k)==h,'SCED review missing exact expanded/spectrum binding: '+k)
    sv=pin(scuc_values_path,scuc_values_sha256);dv=pin(sced_values_path,sced_values_sha256);sp=pin(spectrum_path,spectrum_sha256)
    if isinstance(sp,dict):sp=sp['values']
    require(len(sp)==read(scuc_audit)['full_original_certificate']['integers'],'entire original integer spectrum required')
    require(digest(values)==digest(dv),'SCED values not reviewed expanded map')
    verify_fixed_spectrum(sv,dv,sp)
    r=source_receipt(case,report,values,scuc,sced,logical_available_at=logical_available_at,generated_at=generated_at)
    r.update(SCUC_fixed_discrete_match_verified=True,expanded_witness_source_sha256=sced_values_sha256,full_integer_spectrum_sha256=spectrum_sha256)
    return r
