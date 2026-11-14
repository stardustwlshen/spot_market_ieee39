"""Pure interface checks for integration into a new adapter; no solver invocation.
Not a substitute for source-pinned full mathematical acceptance.
"""
from pathlib import Path
import hashlib,json

def need(ok,msg):
 if not ok:raise ValueError(msg)
def filehash(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def read(p):return json.loads(Path(p).read_bytes())

def selected_da(da):
 da=Path(da).resolve();a=read(da/'ARTIFACTS.json');r=read(da/'RUN.json');checked={}
 need(a.get('status')=='accepted','DA artifacts not accepted')
 need(a.get('run_id') and all(a.get(k)==r.get(k) for k in ('run_id','source_identity','input_identity')),'DA run/source/input identity mismatch')
 def ref(z):
  p=(da/z['path']).resolve();need(p.is_relative_to(da),'DA artifact escapes selected run')
  h=filehash(p);need(h==z['sha256'],'DA artifact hash mismatch');checked[str(p)]=h;return p
 stage=a['stages']['da_sced'];accept=read(ref(stage['acceptance']))
 need(accept.get('status')=='accepted' and accept.get('stage')=='da_sced','DA SCED acceptance status/stage')
 need(all(accept.get(k)==a.get(k) for k in ('run_id','source_identity','input_identity')),'DA acceptance identity mismatch')
 need(accept.get('audit',{}).get('full_KKT',{}).get('passed') is True,'DA accepted full KKT required')
 for k in ('full_names','expanded_response'):
  need(stage[k]==accept['artifacts'][k],'DA witness not bound to acceptance');ref(stage[k])
 for z in accept['artifacts'].values():ref(z)
 for role in ('case','agc','policy'):
  need(a['inputs'][role]==r['inputs'][role],'DA input registry mismatch');ref(a['inputs'][role])
 sp=r.get('source_pins');need(isinstance(sp,dict)and sp,'DA source pins required')
 for p,h in sp.items():need(filehash(p)==h,'DA source changed');checked[p]=h
 checked.update({str(da/'RUN.json'):filehash(da/'RUN.json'),str(da/'ARTIFACTS.json'):filehash(da/'ARTIFACTS.json')})
 return checked

def prepared(ticket,context,window,context_sha256,current_code_pins,expected_request,load):
 need(type(window)is int and 0<=window<96,'window0..95')
 need(ticket.get('schema')=='gd.rt.prepared.v2'and ticket.get('window')==window,'PREPARED window/schema mismatch')
 need(ticket['context_sha256']==context_sha256 and ticket['code_pins']==current_code_pins,'PREPARED context/source mismatch')
 q=load(ticket['request'])
 need(q==expected_request,'PREPARED request is not current boundary')
 need(q['run_id']==context['run_id'] and q['window']==window and q['cutoff_tick']==window*3 and q['delivery_tick']==(window+1)*3,'PREPARED run/window/clock mismatch')
 need(q['case_sha256']==context['refs']['case']['raw_sha256']and q['policy_sha256']==context['refs']['policy']['raw_sha256']and q['sources']['AGC']['sha256']==context['refs']['agc']['raw_sha256'],'PREPARED input identities mismatch')
 template=load(ticket['template']);names=load(ticket['names'])
 need(len(template['variables'])==len(names['variables']) and len(template['rows'])==len(names['constraints']),'PREPARED names/matrix dimensions')
 need(len(set(names['variables']))==len(names['variables']),'duplicate variable names')
 return q,template,names

def acceptance_fields(a,ticket,context,window,context_sha256,current_code_pins,sced_completed,prepared_path):
 need(a.get('schema')=='gd.rt.accepted_pair.v2'and a.get('window')==window and a.get('run_id')==context['run_id'],'accepted pair run/window mismatch')
 need(a['context_sha256']==context_sha256 and a['code_pins']==current_code_pins,'accepted context/source mismatch')
 need(a['request']==ticket['request'] and a['names']==ticket['names'],'accepted request/names not PREPARED')
 need(a['SCED_response']==sced_completed['response'],'accepted SCED response not native receipt')
 need(a['receipt_pins'].get(str(Path(prepared_path)))==filehash(prepared_path),'accepted PREPARED receipt binding')
 for ref in [ticket[k]for k in ('request','template','names')]+[sced_completed[k]for k in ('request','response','log')]:need(ref in a['refs'],'accepted ref missing from authenticated inventory')
 need(a.get('formal')is False and a.get('safety_release')is False,'accepted research status')
 return a
