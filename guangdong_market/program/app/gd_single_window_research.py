"""Read-only single-window evidence and explicit initialized-host prepare entry."""
from pathlib import Path
import json,hashlib,sys,importlib
PINS={'FACADE.json': '798bc85d793fd987d03fa88e2691c5dbd7b29e3a6453e7fd86de8c748a2f6028', 'RESULT.json': '726172df7ae1a111844d1313b992b43df28e2bbc3689634e6123ca7388a21d68', 'SLACK_READOUT.json': 'c05e9e007d43d83ba3ce81fa1a62a3a0cef3b9addb59f20e599508dcaebe2360', 'PRICE_QUALITY_ADDENDUM.json': 'b95df08bcb2e301c2f3d23ec1043dc2394c85d2bc1017489f180a70ec78a9b9d', 'READONLY_SHORTAGE_DIAGNOSIS.json': '278e1293ddfa44ee3b49cc7808b2ba53b61a3b266915fe07c9973ee424d4e090', 'PRICE8_RESULT.json': 'c150dc8a0c09a9fb1396d3b1c2b82a1133067054327585de40f243d001bc8328', 'PRICE8_UNITS.json': 'c03020e4ee6874ba22243a4cdd899e7ad74b9384ce41dad53175143d25f216fd', 'AC48_SUMMARY.json': 'd3da41bcb7a07d5566ac47accbb2e472c3c1407ed2fb2535d7ad03e635862b08', 'AC48_SOURCE.json': '379d04362eeb4b6b7728ba01edf1a8756612e12dd61dea4d682ca0cfdc1fd344'}
DEFAULT_BUNDLE=Path(__file__).parent/'single_window_evidence'
def inspect(bundle):
 root=Path(bundle).resolve();data={}
 for n,h in PINS.items():
  raw=(root/n).read_bytes()
  if hashlib.sha256(raw).hexdigest()!=h:raise ValueError('单窗证据版本不一致：'+n)
  data[n]=json.loads(raw)
 f=data['FACADE.json'];p=data['PRICE8_RESULT.json']
 if f['price']['stage_source_bundle_sha256']!=p['source_bundle_sha256']:raise ValueError('价格与近段来源不一致')
 if p['source_stage']!='near24' or p['execution'] or p['formal']:raise ValueError('非支持的研究单窗')
 units=data['PRICE8_UNITS.json'];shortage=units['first_quarter_shortage_mw']
 ac=data['AC48_SUMMARY.json'];source=data['AC48_SOURCE.json']
 if ac['source_binding_sha256']!=PINS['AC48_SOURCE.json'] or source['executed'] or source['global_ticks']!=[3,50]:raise ValueError('AC预测范围绑定不一致')
 if f['far']['sha256'] not in source['pins'].values():raise ValueError('AC与远段来源不一致')
 return dict(shortage_by_quarter_mw=units['shortage_by_quarter_mw'],total_shortage_mwh=units['total_shortage_mwh'],ac_prediction=ac,near_cells=24,far_cells=24,price_ticks=p['interval'],prices=p['prices_yuan_per_mwh'],shortage_mw=shortage,actual_input_available_at=p['actual_input_available_at'],executed=False,publication=False,settlement=False,full_window_AC=False,facade_status=f['status'],source_bundle_sha256=p['source_bundle_sha256'])
def prepare_registered_window(*,stage_api_root,program_root,host,near_prepared,near_actual,base_run,selected_DA,research_agc_input,output,reuse_far):
 """For an existing worker's live host only; never JSON-restores a host or solves.
 Requires source-verified accepted far output; wrapper does not grant authority.
 """
 if host is None or not hasattr(host,'hooks') or not hasattr(host,'session'):raise ValueError('需要原worker已初始化且完成near接受的真实host，不能由结果JSON创建')
 root=Path(stage_api_root).resolve();entry=root/'price_api/single_window.py'
 if not entry.is_file():raise ValueError('未安装研究单窗API；不能回退旧joint48')
 sys.path.insert(0,str(root/'price_api'))
 try:
  mod=importlib.import_module('single_window')
  if Path(mod.__file__).resolve()!=entry:raise ValueError('不同API模块已加载，请使用独立进程')
  return mod.run_single_window(program_root=program_root,host=host,near_prepared=near_prepared,near_actual=near_actual,base_run=base_run,selected_DA=selected_DA,research_agc_input=research_agc_input,output=output,mode='prepare',execute_far=False,execute_price=False,reuse_far=reuse_far)
 finally:sys.path.pop(0)
def render():
 import streamlit as st
 with st.expander('独立24+24研究单窗：核验已有结果',expanded=False):
  path=st.text_input('单窗证据目录',str(DEFAULT_BUNDLE),key='single_window_source')
  if st.session_state.get('single_window_identity')!=path:
   st.session_state['single_window_identity']=path;st.session_state.pop('single_window_view',None)
  if st.button('核验并读取单窗（不求解）',key='single_window_read'):
   st.session_state.pop('single_window_view',None)
   try:st.session_state['single_window_view']=inspect(path)
   except (ValueError,OSError,KeyError) as exc:st.error(str(exc))
  z=st.session_state.get('single_window_view')
  if z:
   from gd_single_window_loss_view import load as load_loss,comparison_rows
   try:
    loss=load_loss();st.dataframe(comparison_rows(loss),hide_index=True)
   except (ValueError,KeyError,OSError) as exc:loss=None;st.warning(str(exc))
   choice=st.radio('研究结果', ['基准单窗','一次损耗候选'],key='single_window_variant')
   if choice=='一次损耗候选':
    if loss:
     st.error('一次损耗候选仍不安全：PRICE未运行，无候选电价；未执行、未结算。')
     st.caption('48格仅为预测快照。C1技术越限消除，但27格调频余量不满足、电压仍369处越限。独立PRICE纯准备不是已求解电价。')
    return
   st.write('近段24格 → 远段24格预测 → 近段8季度独立研究价；未推进执行。')
   st.error(f"定价模型缺额 {z['shortage_mw']:.6f} MW，属于参考带约束引起的研究惩罚价，不等同SCED物理缺电。")
   st.caption('C3上界为1.1×日前SCED均值：δ=10%为研究参数，未核到广东正式数值；1000罚价仅查到区域附表默认，未认证广东覆盖。')
   st.write('8季度定价模型松弛折算电量（非实际停供） '+str(round(z['total_shortage_mwh'],6))+' MWh（不是MW相加）。')
   st.dataframe([{'季度':i+1,'定价模型缺额 MW':x} for i,x in enumerate(z['shortage_by_quarter_mw'])],hide_index=True)
   st.error('独立单窗48格预测AC：48格收敛、0格安全；不是已执行24小时覆盖。C1出力越限48处，电压越限358处，AGC余量满足0/48。')
   st.caption('原GT电压设定1.0636超过1.06，保留输入冲突；未调整控制、未推进执行。')
   st.caption('输入实际可用时刻未知；无完整窗口AC安全证明；不可执行、发布或正式结算。各数值阶段分别完成，不是一键全链验收。')
   st.dataframe([{'节点平衡约束':k,'研究价 元/MWh':v} for k,v in z['prices'].items()],hide_index=True)
  st.caption('新单窗准备需原计算worker中的已登记host及已接受近段原解。普通页面暂不启动24+24，不能把历史JSON当作运行权限。')
