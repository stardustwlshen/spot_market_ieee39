"""Read-only, fail-closed display. No optimizer, subprocess or AC imports."""
from pathlib import Path
import hashlib
import json

SOURCE = 'guangdong_agc_hour_reinitialized_remaining7_20261112'
MANIFEST_SHA = '03eaa435e24dfef7c3e62cb1ea04a594d125ee1858f0ac71860fe3bba213eb46'

def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def load_verified(workspace):
    root = Path(workspace).resolve() / SOURCE
    manifest = root / 'MANIFEST.json'
    if not manifest.is_file() or _sha(manifest) != MANIFEST_SHA:
        raise ValueError('来源清单缺失或已改动，拒绝显示通过结果')
    for item in json.loads(manifest.read_text())['files']:
        path = (root / item['path']).resolve()
        if not path.is_relative_to(root.resolve()) or not path.is_file() or _sha(path) != item['sha256']:
            raise ValueError('来源文件缺失或已改动，拒绝显示通过结果：' + item['path'])
    def read(name):
        return json.loads((root / name).read_text())
    final = read('FINAL_STAGE_REPORT.json')
    base = 'plan_consistent_candidate_pure/'
    summary = read(base + 'FINAL_SUMMARY.json')
    controls = read(base + 'NORMALIZED_CONTROL_INTERFACE.json')
    if not (final['all12_actual_P_equal_SCED'] and summary['all12_base_case_checks_passed'] and final['full_KKT_passed']):
        raise ValueError('研究结果门禁未通过')
    if any(final[k] for k in ('formal_release', 'security_release', 'payments_enabled')) or not final['PRICE_pending']:
        raise ValueError('结果发布边界不一致')
    rows = []
    for row in summary['results']:
        cell = row['cell']
        result = read(base + f'AC_{cell}/RESULT.json')
        voltages = [bus['vm_pu'] for bus in result['AC']['buses']]
        rows.append({'点':cell, '最低电压pu':min(voltages), '最高电压pu':max(voltages),
                     '正备用MW':row['positive_MW'], '事故备用MW':row['accident_MW'],
                     '计划与AC有功最大差MW':row['max_P_difference_MW'], '基态检查':'通过'})
    return {'final':final, 'controls':controls, 'rows':rows}

def render(workspace=None):
    import streamlit as st
    st.subheader('17–18时研究小时：只读基态AC结果')
    st.warning('独立W67研究链，与午夜新freeRT链分离。不是N-1、暂态或真实AVC动态认证；不构成正式安全放行、投标/调度资格或支付。独立PRICE待完成。')
    st.caption('本模块只读取已固定来源，不发起LP、非线性优化或AC计算，也不修改旧票据。')
    if workspace is None:
        workspace = next((p for p in Path(__file__).resolve().parents if (p / SOURCE).is_dir()), Path(__file__).resolve().parent)
    evidence_root = st.text_input('证据工作目录', value=str(workspace), key='gd_hour_ac_evidence_root')
    st.info('默认不读取历史证据。填写包含已验研究小时目录的工作目录，再点击读取；每次点击均重新逐文件校验，不缓存跳过哈希。')
    selected = st.selectbox('读取时显示的5分钟控制点', list(range(204,216)), key='gd_hour_ac_control_cell')
    if not st.button('读取已验研究小时', key='gd_hour_ac_read_verified'):
        return
    try:
        data = load_verified(evidence_root)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        st.error(str(exc))
        return
    st.success('同一研究小时12个5min基态AC点通过')
    st.write('2026-11-11 17:00–18:00（北京时间）；HiGHS全KKT通过，12点计划/AC实际有功一致。双400 MW门槛、原电压及SOC/爬坡约束未放宽。')
    st.dataframe(data['rows'], hide_index=True)
    st.info('新增无功能力为独立研究设备设计假设，不是原设备实测或广东正式参数。风光pf=.95、储能并网pf=.9，Sn=Pn/pf；同时限制Qcap和低压电流。原装机Pn不随出力调整；零P资源本合同不供Q，资格不变。')
    st.write('旧Q=0路径及失败证据保留。重初始化7点中6点通过，206原非线性诊断失败；206改用205/207已通过控制的算术中点，未新增非线性求解，另做一次真实AC验证通过。之后统一一次HiGHS及12次同源AC终验。')
    with st.expander('控制指令与结果（热机VG是命令，Qg是潮流结果）', expanded=False):
        st.caption('热机Qg不能与VG作为两个独立指令同时施加。逆变器Q是研究指令；每点启用状态重新校验。')
        row = next(r for r in data['controls']['rows'] if r['global_cell']==selected)
        st.dataframe(row['thermal_controls'], hide_index=True)
        st.json(row)
    with st.expander('技术诊断与来源校验', expanded=False):
        st.write('已逐文件核对冻结清单SHA256；缺失或变更即拒绝通过展示。')
        st.json(data['final'])
