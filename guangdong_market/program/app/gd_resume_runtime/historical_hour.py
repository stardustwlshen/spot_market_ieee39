"""Narrow, owned reuse of already selected declared H1-H24 research facts.
Read-only effective case recovery only; no solver/KKT replay, safety approval or old capability restore.
Consumers dispatch only this exact private type; never register it as a LivePhase.
"""
from pathlib import Path
from copy import deepcopy
import json,hashlib,os
_KEY=object()
_OWNED={}
read=lambda p:json.loads(Path(p).read_bytes())
from content_hash import sha
def need(ok,msg):
 if not ok:raise ValueError(msg)
def _pinned(path,pins):
 p=Path(path).resolve();value=pins.get(str(p))
 need(value is not None and sha(p)==value,'历史小时来源未绑定或改变：'+str(p));return str(p),value
def exact_declaration_relocation(previous,current,source_pins):
 """Only equivalent, pinned constructor-input path relocation, no value edits."""
 if previous==current:return {},None
 a,b=deepcopy(previous),deepcopy(current)
 need(isinstance(a.get('research_extension'),dict)and isinstance(b.get('research_extension'),dict),'当前声明与历史声明不等')
 old=a['research_extension'].get('source');new=b['research_extension'].get('source')
 need(isinstance(old,str)and isinstance(new,str)and old!=new,'声明差异不是来源路径迁移')
 before_hash=a['research_extension'].get('source_sha256');after_hash=b['research_extension'].get('source_sha256')
 need(isinstance(before_hash,str)and before_hash==after_hash,'声明来源内容hash改变')
 a['research_extension']['source']=new
 need(a==b,'除来源路径外的声明字段改变')
 guards={}
 for path in (old,new):
  resolved=Path(path).resolve();key=str(resolved)
  need(source_pins.get(key)==before_hash and sha(resolved)==before_hash,'声明迁移来源未绑定或实际内容改变：'+key)
  guards[key]=before_hash
 return guards,dict(kind='exact_pinned_research_input_path_relocation',old_source=old,new_source=new,sha256=before_hash,all_other_fields_identical=True,new_AGC_award=False,new_native_calls=0)

def validate_evidence(folder,hour,source_pins,*,branch,declaration):
 """Pure immutable-chain verification. No passed flag grants live access."""
 from gd_post_scuc_runtime.phase_runtime import digest,stamp
 folder=Path(folder).resolve();need(type(hour)is int and 1<=hour<=24,'本次仅支持已完成declared H1-H24历史导入')
 bound={}
 for path,value in source_pins.items():
  key=str(Path(path).resolve());need(key not in bound or bound[key]==value,'历史来源别名hash冲突');bound[key]=value
 source_pins=bound
 w=4*hour-2;guards={};files={}
 for name in (f'H{hour}_RESEARCH_AGC_RECEIPT.json',f'H{hour}_AGC_MATH_CANDIDATE.json','actual/ACCEPTED_PAIR.json','BEFORE_HANDOFF.json','RESULT.json','EXACT_CARRY.json','prepared/SUMMARY.json'):
  p=folder/name;k,h=_pinned(p,source_pins);guards[k]=h;files[name]=read(p)
 export=files[f'H{hour}_RESEARCH_AGC_RECEIPT.json'];candidate=files[f'H{hour}_AGC_MATH_CANDIDATE.json'];pair=files['actual/ACCEPTED_PAIR.json'];gate=files['BEFORE_HANDOFF.json'];result=files['RESULT.json'];carry=files['EXACT_CARRY.json']
 need(export['schema']=='gd.agc.accepted_research_display.v1'and export['export_kind']=='selected_after_handoff_research_display','缺少交接后已选择历史回执')
 need(export['branch_id']==candidate['branch_id']==result['branch_id']==gate['branch_id']==branch,'历史小时逻辑分支不匹配')
 need(export['hour_index']==candidate['hour_index']==hour and result['window']==gate['window']==w,'历史小时时序不匹配')
 declaration_guards,declaration_relocation=exact_declaration_relocation(export['declaration'],declaration,source_pins);guards.update(declaration_guards)
 need(export['receipt_digest']==candidate['receipt_digest']==digest(export['receipt'])and export['receipt']==candidate['receipt'],'候选和已选择回执不一致')
 need(export['phase_proof']==candidate['phase_proof']==pair['post_SCUC_phase'],'候选/接受数学证明不一致')
 need(export['phase_proof']['receipt']==export['receipt_digest']and export['source']==export['phase_proof']['source'],'调频阶段来源不一致')
 need(candidate['consumable']is False and candidate['selected']is False,'历史候选状态被重写')
 sel=export['selection'];need(sel['handoff_complete']is True and sel['kind']=='explicit_counterfactual_research'and sel['before_handoff']==gate,'历史完整门禁和选择不一致')
 need(gate['carry_advance_allowed']is True and gate['basis']=='explicit counterfactual simulation only','历史门禁未允许研究推进')
 need(sel['candidate_sha256']==sha(folder/f'H{hour}_AGC_MATH_CANDIDATE.json'),'选择未绑定候选原件')
 need(Path(sel['carry_path']).resolve()==folder/'EXACT_CARRY.json'and sel['carry_sha256']==sha(folder/'EXACT_CARRY.json')==result['handoff']['carry_sha256'],'交接边界来源不一致')
 need(Path(result['handoff']['carry_path']).resolve()==folder/'EXACT_CARRY.json','交接结果指向错误边界')
 need(pair['status']=='accepted_research_pair'and pair['prepared_sha256']==sha(folder/'prepared/SUMMARY.json'),'历史数学输入不一致')
 for stage in ('SCUC','SCED'):
  for name in ('request.json.gz','response.json.gz','JOB.json','COMPLETED.json'):
   p=folder/'actual'/stage/name;k,h=_pinned(p,source_pins);guards[k]=h
  need(pair[stage+'_response_sha256']==sha(folder/'actual'/stage/'response.json.gz'),'历史原始解改变')
 transitive={}
 for pins in (export['source_pins'],candidate['source_pins'],gate['source_pins'],carry['source_pins']):
  for k,v in pins.items():
   need(k not in transitive or transitive[k]==v,'历史传递来源冲突：'+k);transitive[k]=v
 for k,v in transitive.items():need(sha(k)==v,'历史传递来源改变：'+k)
 for obj in (export,candidate,gate,result,carry,sel):need(obj.get('safety_release')is False and obj.get('formal')is False,'历史证据不能冒充安全或正式授权')
 receipt=export['receipt'];need(receipt['branch_id']==branch and receipt['published_at']==declaration['as_of']and receipt['hour_start']==declaration['hour_start'],'历史回执时间不一致')
 need(stamp(receipt['published_at'])<=stamp(carry['cutoff']),'尚未发布的小时不能消费')
 need(carry['cutoff_tick']==3*(w+1)and result['handoff']['ledger_id']==carry['event_ledger_sha256'],'交接账本或时钟不一致')
 return dict(declaration_relocation=declaration_relocation,hour=hour,window=w,receipt=deepcopy(receipt),AGC_semantics=deepcopy(export.get('AGC_semantics',{})),phase_proof=deepcopy(export['phase_proof']),guards=guards,carry_path=str(folder/'EXACT_CARRY.json'),carry_sha256=sha(folder/'EXACT_CARRY.json'),ledger_id=carry['event_ledger_sha256'],receipt_path=str(folder/f'H{hour}_RESEARCH_AGC_RECEIPT.json'),receipt_sha256=sha(folder/f'H{hour}_RESEARCH_AGC_RECEIPT.json'))

class HistoricalSelected:
 __slots__=('controller','identity','hour','payload','digest','guards','evidence','evidence_sha256')
 def __init__(self,key,controller,z,evidence):
  from gd_post_scuc_runtime.phase_runtime import digest
  need(key is _KEY,'历史事实构造能力缺失');h=controller.h;self.controller=controller;self.identity=(id(h),id(h.session),h.branch_id,getattr(h,'execution_attempt_id',None));self.hour=z['hour'];self.payload=dict(receipt=deepcopy(z['receipt']),AGC_semantics=deepcopy(z['AGC_semantics']),phase_proof=deepcopy(z['phase_proof']),branch_id=h.branch_id,historical_selected_fact=True,safety_release=False,effective_case=deepcopy(z['effective_case']),effective_case_restore_proof=deepcopy(z['effective_case_restore_proof']));self.digest=digest(self.payload);self.guards=dict(z['guards']);self.evidence=Path(evidence);self.evidence_sha256=sha(evidence)

def verified_history(record):
 from gd_post_scuc_runtime.phase_runtime import digest
 need(type(record)is HistoricalSelected and _OWNED.get(id(record))is record,'未登记的历史事实')
 c=record.controller;h=c.h;need(record.identity==(id(h),id(h.session),h.branch_id,getattr(h,'execution_attempt_id',None))and c.accepted.get(record.hour)is record,'历史事实宿主/小时被替换')
 h.session.verify_sources()
 for p,v in record.guards.items():need(sha(p)==v,'历史事实证据改变')
 need(sha(record.evidence)==record.evidence_sha256 and digest(record.payload)==record.digest,'历史事实导入记录改变')
 return deepcopy(record.payload)

def import_after_completed(controller,hour,folder,*,source_pins,output):
 """Called AFTER transfer_completed verified/registered the matching physical carry."""
 from gd_post_scuc_runtime.controller import Controller
 need(type(controller)is Controller and controller.h.hooks.post_scuc is controller,'当前真实Controller必需');c=controller;h=c.h;w=4*hour-2
 need(h.mode=='native'and h.pending is None and h.window==w+1,'必须先完成该历史物理交接')
 need(hour not in c.accepted and hour not in h.agc.issued and not c.candidates,'已发布小时不可替换')
 need(getattr(h.before_handoff_gate,'counterfactual',False)is True,'当前接续须明确反事实研究选择')
 p,hs,d=c.declarations[hour];need(sha(p)==hs,'当前声明改变')
 z=validate_evidence(folder,hour,source_pins,branch=h.branch_id,declaration=d)
 from .historical_agc_context import restore
 recovered=restore(folder,z,source_pins,decode=h.session.program.engine.loads)
 z['effective_case']=recovered['effective_case'];z['effective_case_restore_proof']=recovered['proof'];z['guards'].update(recovered['guards'])
 prepared,actual=h.accepted_source(w);need(Path(prepared).resolve()==Path(folder).resolve()/'prepared'and Path(actual).resolve()==Path(folder).resolve()/'actual','物理历史源不匹配')
 need(Path(h.cp).resolve()==Path(z['carry_path'])and h.hooks.carries.get(str(Path(h.cp).resolve()))==z['carry_sha256']and h.lid==z['ledger_id'],'当前宿主未登记对应历史边界')
 output=Path(output);output.mkdir(parents=True,exist_ok=True);evidence=output/f'H{hour}_HISTORICAL_SELECTED_IMPORT.json'
 payload=dict(declaration_relocation=z.get('declaration_relocation'),schema='gd.agc.historical_selected_import.v1',execution_attempt_id=h.execution_attempt_id,logical_branch=h.branch_id,hour=hour,window=w,historical_receipt=z['receipt_path'],historical_receipt_sha256=z['receipt_sha256'],historical_phase_proof=z['phase_proof'],source_pins=z['guards'],existing_handoff_fact=True,new_live_phase=False,new_handoff=False,new_native_calls=0,new_KKT_replay=False,effective_case_restore_proof=z['effective_case_restore_proof'],safety_release=False,formal=False)
 with evidence.open('x')as f:json.dump(payload,f,indent=2);f.flush();os.fsync(f.fileno())
 record=HistoricalSelected(_KEY,c,z,evidence);_OWNED[id(record)]=record;c.accepted[hour]=record;c.planning_evidence.pop(hour,None);c.planning_tokens.pop(hour,None)
 from gd_post_scuc_runtime import consumers
 consumers.register_historical_context(prepared,actual,record)
 return payload


def import_selected(x,w,folder,*,source_pins,out):
 need(w%4==2,'仅小时到期窗口')
 return import_after_completed(x.post_controller,(w+2)//4,folder,source_pins=source_pins,output=Path(out)/'HISTORICAL_HOUR_IMPORTS')
