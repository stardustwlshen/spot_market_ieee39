"""Explicit candidate evidence identity; never inherits baseline prices."""
from pathlib import Path
import json,hashlib
PINS={'RESULT.json': '3d6deef83c891895579299c09bbde1f201c03cd71bf88cd8dddd38962a69bf06', 'COMPARISON.json': '34af68507c72c5ca8ba4f9bf7178c1ebd276dfac4130097dd717446f9db359c4', 'PREPARE.json': '0e7983e8b70fef7969aad41febf89453da68677112dc7e56e1455f8aa2d8c14f', 'AC_SOURCE.json': 'f2b0de3466c5a535299b4124b607c768f09ed666814958fe00b154ff5ee5e870', 'NEAR_ACCEPTED.json': 'ff64a46c5c64a7a1bca8679e7b56137ccedb6eff636d11e32d260e55eab26a40', 'FAR_ACCEPTED.json': '0855cc6cde78af34d2212d2c33c332cac0da35f66888014b826a45301460a5bc', 'CARRY.json': 'aa5e45342e9af0bd73db00f72f6c61979abcf8436a250c22eaa237541db2d01a', 'INSTANCE.json': '533316b9417915dc009cb048f61a1254364ebfbdedb06c6f1cec450cd786f2a7', 'CONTEXT.json': '82272b2d874f1bd9fdea1388c1d762c0c4a21df37016ab0a1cf5f89f20c47618', 'BASELINE_CARRY.json': 'b8e1337a62eee2de38b0bff64db750ee974ad90df0499dc53abee06e6f226933'}
BUNDLE=Path(__file__).parent/'single_window_loss_evidence'
def load(root=None):
 root=Path(root or BUNDLE);z={}
 for n,h in PINS.items():
  raw=(root/n).read_bytes()
  if hashlib.sha256(raw).hexdigest()!=h:raise ValueError('损耗候选来源版本不一致：'+n)
  z[n]=json.loads(raw)
 result=z['RESULT.json'];inst=z['INSTANCE.json'];carry=z['CARRY.json'];ctx=z['CONTEXT.json'];prep=z['PREPARE.json'];source=z['AC_SOURCE.json'];summary=z['COMPARISON.json']
 if inst['context_sha256']!=PINS['CONTEXT.json'] or inst['carry_sha256']!=PINS['CARRY.json']:raise ValueError('候选实例未绑定当前输入')
 if prep['record']['carry_path']!=inst['base_run']+'/INITIAL_CARRY.json' or prep['record']['carry_sha256']!=inst['carry_sha256']:raise ValueError('不同任务实例的carry')
 if inst['selected_DA']!=carry['selected_DA'] or ctx['selected_DA']!=carry['selected_DA']:raise ValueError('候选DA不一致')
 baseline=z['BASELINE_CARRY.json']
 if any(carry[k]!=baseline[k] for k in ('selected_DA','selected_DA_run_id','seed_case_sha256')):raise ValueError('比较对象不是同DA/case')
 if result['near']!=z['NEAR_ACCEPTED.json'] or result['near']['prepared_sha256']!=prep['record']['summary_sha256']:raise ValueError('near接受件不一致')
 for n in ('NEAR_ACCEPTED.json','FAR_ACCEPTED.json'):
  if PINS[n] not in source['pins'].values():raise ValueError('AC来源不属于此接受件')
 if summary['AC']['source_binding_sha256']!=PINS['AC_SOURCE.json']:raise ValueError('AC摘要不属于此候选')
 if result['PRICE'] or result['execution'] or result['publication'] or result['status']!='blocked_unsafe':raise ValueError('不是支持的未执行阻断候选')
 return dict(identity=(inst['initialization_instance_id'],inst['base_run'],inst['context_sha256'],carry['selected_DA'],result['near']['SCED_response_sha256']),status='blocked_unsafe',prices=None,PRICE_calls=0,execution=False,publication=False,comparison=summary)
def comparison_rows(z):
 s=z['comparison'];return [dict(指标='C1技术出力越限格数',基准=48,一次损耗候选=s['counts'].get('GENERATOR_P_LIMIT',0)),dict(指标='调频余量满足格数',基准=0,一次损耗候选=s['AGC_preserved']),dict(指标='电压越限处数',基准=358,一次损耗候选=s['counts']['BUS_VOLTAGE_LIMIT']),dict(指标='静态安全格数',基准=0,一次损耗候选=s['AC']['safe'])]
