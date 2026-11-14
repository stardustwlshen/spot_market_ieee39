"""Read-only rejected baseline research branch; never maps it to a selected edited run."""
from pathlib import Path
import json,hashlib
PIN={
 'guangdong_short_first_ac_v1_20261112':'ec157381280c36fa40b30000576689954df92c1a9ee88c0bf164736f92327f9a',
 'guangdong_short_loss_ac_loop_v1_20261112':'16ef6dc859e5fc64d8f01ce6ee46a3de2f987dceefadd0a7dca76afd69822152',
 'guangdong_short_qv_zeroequiv_v2_20261112':'59e33108fecdb768374027dae5e715d8c429f67ef36334a15ca4a94da60d74dc',
 'guangdong_short_pqv_redispatch_v3_20261112':'da22bbfc7673d976ca8da96a991036fe6f62a21da43c47f65ecc5c8fccc37a15'}
def load(root):
 root=Path(root).resolve();out=[]
 for name,h in PIN.items():
  d=root/name;p=d/'MANIFEST.json'
  if not p.is_file()or hashlib.sha256(p.read_bytes()).hexdigest()!=h:raise ValueError('来源清单缺失或已变化，拒绝显示该历史检查')
  for item in json.loads(p.read_bytes())['files']:
   f=(d/item['path']).resolve()
   if not f.is_relative_to(d)or not f.is_file()or hashlib.sha256(f.read_bytes()).hexdigest()!=item['sha256']:raise ValueError('来源文件缺失或变化：'+item['path'])
  z=json.loads((d/('SUMMARY.json'if name.startswith('guangdong_short_first')else'FINAL_STATUS.json')).read_bytes())
  if z.get('security_release')is not False or z.get('formal_release')is not False:raise ValueError('拒绝分支的放行状态不一致')
  out.append(z)
 return out

def render():
 import streamlit as st
 st.subheader('基准短域首点：安全检查拒绝结果')
 st.warning('这里是原基准研究链00:15–00:20的检查，不是任意编辑场景的结论，也不是另一个17–18时安全研究分支。数学出清成功不等于可安全执行。')
 default=next((p for p in Path(__file__).resolve().parents if (p/next(iter(PIN))).is_dir()),Path(__file__).resolve().parent)
 root=st.text_input('基准安全证据所在目录',str(default),key='ssafe_root')
 if not st.button('读取基准安全拒绝证据（0计算）',key='ssafe_read'):return
 try:a,b,c,d=load(root)
 except Exception as e:st.error(str(e));return
 st.error('安全检查未通过：不发安全票据，不把失败末值写入执行账或认证价格。')
 st.dataframe([
  {'阶段':'原Q0真实AC','检查结果':'收敛但不安全','关键事实':f"V范围{a['Vmin']:.6f}–{a['Vmax']:.6f} pu；网损{a['actual_loss_MW']:.6f} MW使平衡机越AGC后上限"},
  {'阶段':'同源损耗反馈SCED + AC','检查结果':'有功越界已解除，电压仍未过','关键事实':f"全KKT通过；计划/AC有功差仍{b['original_Q0_AC']['max_plan_actual_P_difference_MW']:.6f} MW"},
  {'阶段':'等价Q/V数值诊断','检查结果':'末值拒绝，未发候选AC','关键事实':f"{c['final_review']['nfev']}次评估；原电压界和设备能力不变"},
  {'阶段':'合法局部P/Q/V诊断','检查结果':'末值拒绝，未发新LP/AC','关键事实':f"{d['nfev']}次评估；欠压且零P禁Q检查失败"}],hide_index=True)
 st.caption('原双400 MW、设备容量、资格和零P禁Q合同未放宽。只有数值搜索方法及合法P域变化；失败不证明整个市场物理无解。未继续盲搜，不用epsilon功率解锁Q。')
 with st.expander('证据与技术边界'):
  st.write('已校验四个冻结阶段的清单及文件哈希。这里不执行潮流或优化。')
  st.json({'original_AC_voltage_bounds':{'min':a['Vmin'],'max':a['Vmax']},'loss_feedback_full_KKT':b['full_SCED_KKT']['passed'],'Q_V_diagnostic':c['final_review'],'P_Q_V_diagnostic':d['final_review'],'formal_release':False,'security_release':False,'physical_infeasibility_proved':False})
