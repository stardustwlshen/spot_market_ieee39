"""Fresh-process near24 prepare only. Explicit accepted DA, no native UI path."""
from pathlib import Path
import json,sys,subprocess

def prepare(*,selected,base_run,worker_root,stage_api_root,price_api_root,output,day_declarations):
 if selected.get('route')=='readonly_legacy_accepted':
  from gd_accepted_da_readonly import verify
  proof=verify(selected['run'],base_run)
  if proof['ARTIFACTS_sha256']!=selected['ARTIFACTS_sha256'] or proof['context_sha256']!=selected['context_sha256']:raise ValueError('已选择日前来源已变更')
  da=proof['run']
 else:
  from gd_da_binding import verify_selection
  da=verify_selection(selected)
 root=Path(__file__).resolve().parent.parent
 paths=[Path(base_run),Path(worker_root)/'new_worker.py',Path(stage_api_root)/'stage_api.py',Path(price_api_root)/'single_price.py',Path(worker_root)/'new_base.py',Path(day_declarations)]
 if not all(p.exists() for p in paths):raise ValueError('缺少已登记基础运行或研究worker/API安装路径；不能以结果快照替代')
 if Path(output).exists():raise ValueError('请选择未存在的新准备目录，避免覆盖')
 args=dict(program_root=str(root),base_run=base_run,worker_root=worker_root,stage_api_root=stage_api_root,price_api_root=price_api_root,selected_DA=da,research_agc_input=None,output=output,day_declarations=day_declarations)
 for phase in ('initialize','prepare'):
  result=subprocess.run([sys.executable,str(Path(__file__).resolve()),json.dumps(args),phase],capture_output=True,text=True,timeout=180)
  if result.returncode:raise ValueError('研究单窗'+phase+'未通过，保留已生成来源，不自动重试：'+(result.stderr or result.stdout)[-2500:])
 return json.loads(result.stdout.strip().splitlines()[-1])
def render(selected=None):
 import streamlit as st
 with st.expander('准备研究单窗（不求解/不执行）'):
  st.caption('创建独立初始任务并准备近24格，0求解；不继承旧运行进度。原基础运行仅核对日前来源，日声明必须匹配该日前输入。')
  args={}
  runtime=Path(__file__).resolve().parent/'research_runtime/single_window'
  args.update(worker_root=str(runtime/'worker'),stage_api_root=str(runtime/'stage'),price_api_root=str(runtime/'price'))
  installed=all(p.is_file() for p in [runtime/'worker/new_worker.py',runtime/'stage/stage_api.py',runtime/'price/single_price.py'])
  if not installed:st.info('研究运行组件尚未随此软件安装，准备功能暂不可用。已有单窗结果仍可核验查看；不要求填写内部API路径。')
  for key,label in [('base_run','已登记基础运行目录（非结果快照）'),('day_declarations','明确研究日声明文件'),('output','新的准备输出目录')]:args[key]=st.text_input(label,key='swprep_'+key)
  if selected is None:
   da_path=st.text_input('明确选择已接受日前目录',key='swprep_da')
   selection_input=(da_path,args['base_run'])
   if st.session_state.get('swprep_selection_input')!=selection_input:
    st.session_state['swprep_selection_input']=selection_input;st.session_state.pop('swprep_selected',None)
   if st.button('核验已接受日前来源',disabled=not(da_path and args['base_run']),key='swprep_verify'):
    try:
     from gd_accepted_da_readonly import verify
     st.session_state['swprep_selected']=verify(da_path,args['base_run'])
    except Exception as exc:st.session_state.pop('swprep_selected',None);st.error(str(exc))
   selected=st.session_state.get('swprep_selected')
  identity=(json.dumps(selected,sort_keys=True),tuple(sorted(args.items())))
  if st.session_state.get('swprep_identity')!=identity:
   st.session_state['swprep_identity']=identity;st.session_state.pop('swprep_result',None);st.session_state.pop('swprep_instance',None)
  if st.button('准备研究单窗（0求解）',disabled=not(installed and selected and all(args.values())),key='swprep_start'):
   try:
    z=prepare(selected=selected,**args);st.session_state['swprep_result']=z;st.session_state['swprep_instance']=z['initialization']['instance'];st.success('独立初始任务及近24格已准备，已停止在首个求解之前。')
   except Exception as exc:
    st.session_state.pop('swprep_result',None);st.session_state.pop('swprep_instance',None);st.error(str(exc))
  if 'swprep_result' in st.session_state:st.json(st.session_state['swprep_result'])
if __name__=='__main__':
 import importlib.util,highspy
 args=json.loads(sys.argv[1]);worker=Path(args.pop('worker_root')).resolve()/'new_worker.py'
 def denied(*a,**k):raise RuntimeError('此准备入口禁止native求解或子进程启动')
 highspy.Highs.run=denied
 class DeniedPopen(subprocess.Popen):
  def __init__(self,*a,**k):denied()
 subprocess.Popen=DeniedPopen
 spec=importlib.util.spec_from_file_location('_single_window_prepare_worker',worker);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
 days=args.pop('day_declarations');outer=Path(args['output']).resolve()
 if sys.argv[2]=='initialize':
  outer.mkdir(parents=True)
  spec2=importlib.util.spec_from_file_location('_new_base_factory',worker.parent/'new_base.py');factory=importlib.util.module_from_spec(spec2);spec2.loader.exec_module(factory)
  initialized=factory.initialize_new_research_base(program_root=args['program_root'],selected_DA=args['selected_DA'],day_declarations=days,output=outer/'base',research=True)
  print(json.dumps(initialized));sys.exit(0)
 if sys.argv[2]!='prepare':raise ValueError('unknown preparation phase')
 base=outer/'base';instance=json.loads((base/'INITIALIZATION_INSTANCE.json').read_bytes());receipt=json.loads((base/'NEW_INITIALIZATION_RECEIPT.json').read_bytes())
 import hashlib
 if instance['program_root']!=args['program_root'] or instance['selected_DA']!=args['selected_DA'] or instance['base_run']!=str(base) or instance['context_sha256']!=hashlib.sha256((base/'SHORT_CONTEXT.json').read_bytes()).hexdigest():raise ValueError('initialization instance changed')
 initialized=dict(base_run=str(base),receipt=receipt,instance=instance)
 args['base_run']=str(base);args['output']=str(outer/'prepared')
 result=m.run_new_window(**args,mode='research_prepare',execute_native=False);result['initialization']=initialized
 (outer/'UI_RESULT.json').write_text(json.dumps(result,indent=2));print(json.dumps(result))
