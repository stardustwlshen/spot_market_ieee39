"""Immutable scenario CLI bridge; no model/solver import."""
from pathlib import Path
import hashlib,json,subprocess,sys,tempfile

def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def call(core,*args):
 helper=Path(core).resolve().parent/'scenario_inputs.py'
 if not helper.is_file():raise ValueError('此引擎尚无场景编译接口；不能用旧CSV替代')
 p=subprocess.run([sys.executable,'-B',str(helper),*map(str,args)],capture_output=True,text=True,timeout=120)
 if p.returncode:raise ValueError(p.stderr[-4000:]or p.stdout[-4000:])
 return json.loads(p.stdout)
def defaults(core):return call(core,'defaults')
def validate(core,directory):
 root=Path(directory).resolve();m=call(core,'validate','--scenario',root)
 saved=json.loads((root/'SCENARIO.json').read_bytes())
 if m!=saved:raise ValueError('场景验证回执与保存manifest不一致')
 if m.get('schema')!='gd.user_scenario.v1' or m.get('research_only')is not True:raise ValueError('需要完整研究场景manifest')
 pins={}
 for rel,h in m['files'].items():
  p=(root/rel).resolve()
  if not p.is_relative_to(root) or not p.is_file()or sha(p)!=h:raise ValueError('场景文件缺失/越界/哈希变化：'+rel)
  pins[rel]=h
 for key in ('bundle','pricing_spec','rt_assets'):
  if m['entries'][key]not in pins:raise ValueError('入口未绑定：'+key)
 return dict(directory=str(root),manifest_sha256=sha(root/'SCENARIO.json'),identity=m['identity'],files=pins,entries=m['entries'],manifest=m)
def compile_scenario(core,spec,output):
 out=Path(output).resolve()
 if out.exists():raise ValueError('必须另存到不存在的场景版本目录')
 with tempfile.TemporaryDirectory(prefix='gd-ui-spec-')as td:
  p=Path(td)/'spec.json';p.write_text(json.dumps(spec,ensure_ascii=False,allow_nan=False));call(core,'compile','--spec',p,'--output',out)
 return validate(core,out)
def render_editor(core,disabled=False):
 import streamlit as st
 import pandas as pd
 with st.expander('研究场景 / 报价编辑（另存新版本，不改原模板）',expanded=True):
  st.caption('六段价格元/MWh、最低出力独立报价元/MWh；光伏可用容量系数0..1。400/400、初态和资格按当前模板配置。旧编辑器CSV不兼容。')
  source=st.text_input('载入已有场景目录（可选）','',key='gd_scenario_load')
  if st.button('载入模板默认值 / 已有场景',disabled=disabled):
   try:
    if source:
     z=validate(core,source);spec=json.loads((Path(source)/z['manifest']['spec_file']).read_bytes())
    else:spec=defaults(core)
    st.session_state['gd_draft']=spec;st.session_state['gd_draft_revision']=st.session_state.get('gd_draft_revision',0)+1
   except Exception as e:st.error(str(e))
  if 'gd_draft'in st.session_state:
   d=st.session_state['gd_draft'];rev=st.session_state['gd_draft_revision']
   name=st.text_input('场景名称',d['name'],key=f'gd_name{rev}',disabled=disabled);desc=st.text_area('说明',d['description'],key=f'gd_desc{rev}',disabled=disabled)
   solar=st.number_input('光伏可用容量系数（研究）',0.,1.,float(d['solar_capacity_factor']),step=.05,key=f'gd_solar{rev}',disabled=disabled)
   rows=[dict(resource=g,**{f'段{k+1}':float(v)for k,v in enumerate(pr)},最低出力价=float(d['minimum_adjustable_prices'][g]))for g,pr in d['thermal_offer_prices'].items()]
   table=st.data_editor(pd.DataFrame(rows),disabled=True if disabled else ['resource'],hide_index=True,key=f'gd_quotes{rev}',column_config={k:st.column_config.NumberColumn(min_value=0.,max_value=1800.)for k in ['段1','段2','段3','段4','段5','段6','最低出力价']})
   fingerprint=hashlib.sha256(json.dumps(dict(name=name,description=desc,solar=solar,quotes=table.to_dict('records')),sort_keys=True,allow_nan=True).encode()).hexdigest()
   if st.session_state.get('gd_draft_fingerprint') not in (None,fingerprint):st.session_state.pop('gd_preflight',None)
   st.session_state['gd_draft_fingerprint']=fingerprint
   st.caption('未保存草稿不会传入模型；请另存新版本，再在下方选择该目录。修改草稿会使已有预检失效。')
   target=st.text_input('另存新场景版本目录','',key='gd_scenario_save',disabled=disabled)
   if st.button('校验并另存新版本',disabled=disabled):
    try:
     if not target.strip():raise ValueError('填写新的场景目录')
     spec=dict(schema='gd.user_scenario.spec.v1',name=name,description=desc,solar_capacity_factor=solar,thermal_offer_prices={z['resource']:[float(z[f'段{k}'])for k in range(1,7)]for z in table.to_dict('records')},minimum_adjustable_prices={z['resource']:float(z['最低出力价'])for z in table.to_dict('records')})
     z=compile_scenario(core,spec,target);st.session_state['gd_selected_scenario']=z['directory'];st.success('已保存并选中新场景：'+z['directory']);st.json(z['manifest']);st.session_state.pop('gd_preflight',None)
    except Exception as e:st.error(str(e))
 return st.text_input('本次运行场景目录（空=引擎原模板；resume必须空）','',key='gd_selected_scenario',disabled=disabled)
