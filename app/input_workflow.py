"""只读中文业务展示；内部报价、成本与导出字段保持不变。"""
import math
import re
import yaml
import pandas as pd
import streamlit as st

CURVE_CN = {'segments': '发电报价', 'dis_segments': '放电报价', 'ch_segments': '充电报价',
            'gen_segments': '抽蓄发电报价', 'pump_segments': '抽水报价'}
COST_CN = {'scalar_marginal_cost_yuan_per_mwh': '边际发电成本（元/MWh）',
           'fixed_online_cost_yuan_per_h': '在线成本（元/h）',
           'fixed_startup_cost_yuan_per_start': '启动成本（元/次）',
           'discharge_degradation_yuan_per_mwh': '放电退化成本（元/MWh）',
           'generation_vom_yuan_per_mwh': '抽蓄发电运维成本（元/MWh）',
           'gen_start_yuan_per_unit_start': '发电启动成本（元/台次）',
           'pump_start_yuan_per_unit_start': '抽水启动成本（元/台次）'}
BASIS_CN = {'fixed_marginal_cost_yuan_per_mwh': '固定边际成本（元/MWh）',
    'original_hhv_derived_marginal_cost_yuan_per_mwh': '原燃料热耗测算值（元/MWh）',
    'offer_strategy_markups_yuan_per_mwh': '分段报价加价（元/MWh）',
    'fuel_hhv_yuan_per_gj': '燃料价格（元/GJ，高位热值）',
    'heat_rate_hhv_gj_per_mwh': '热耗（GJ/MWh，高位热值）',
    'variable_om_yuan_per_mwh': '变动运维（元/MWh）',
    'water_opportunity_yuan_per_mwh': '水价值机会成本（元/MWh）',
    'technical_roundtrip_efficiency': '充放循环效率（比例）',
    'fixed_discharge_expense_yuan_per_mwh': '放电成本（元/MWh）',
    'replacement_cost_including_losses': '含损耗替代能量成本（元/MWh）',
    'reservation_yuan_per_mwh': '保留价值（元/MWh）',
    'discharge_anchor': '放电报价基准（元/MWh）',
    'charge_willingness_anchor': '充电意愿基准（元/MWh）',
    'strategy_increments_yuan_per_mwh': '策略加价（元/MWh）',
    'opportunity_increments_yuan_per_mwh': '机会成本加价（元/MWh）',
    'fixed_intercept_yuan_per_h': '报价固定项（元/h）'}


def workflow_steps(active=1):
    steps = [('核对固定成本', '设备参数 → 运行成本档案'), ('形成主体报价', '主体报价 → 选择主体、预览或手工编辑'),
             ('校验并保存', '提交为新版本；保留旧版本'), ('冻结输入并运行', '运行页 → 核对版本、范围与参数')]
    for i, (column, (title, detail)) in enumerate(zip(st.columns(4), steps), 1):
        with column:
            with st.container(border=True):
                st.markdown(f"{'**' if i == active else ''}{i} · {title}{'**' if i == active else ''}")
                st.caption(detail)


def business_error(summary, exc, hint=None):
    st.error(summary + ('。' + hint if hint else ''))
    with st.expander('技术详情（原始诊断，供排查）', expanded=False):
        st.code(str(exc), language=None)


def launch_error(exc):
    """Explain the actual launch phase without guessing an input/solver cause."""
    from smm.process_control import LaunchError
    import traceback
    started = isinstance(exc, LaunchError) and exc.process_started
    cause = exc.cause if isinstance(exc, LaunchError) else exc
    if started:
        summary = "后台进程已创建，但启动记录未完整确认。请查看下方运行状态与日志，暂勿重复启动。"
    elif isinstance(cause, (PermissionError, FileNotFoundError)):
        summary = "启动前的文件或目录操作失败，请核对下方路径及访问权限。"
    elif isinstance(cause, (ImportError, ModuleNotFoundError)) or "依赖" in str(cause):
        summary = "启动环境不完整，请核对当前 Python 环境和项目依赖。"
    elif isinstance(cause, (ValueError, TypeError, KeyError)):
        summary = "启动前输入校验未通过，请按技术详情修正对应内容。"
    else:
        summary = "启动请求未完成，请按下方具体原因处理后再试。"
    st.error(summary)
    with st.expander("技术详情（启动阶段与原始诊断）", expanded=True):
        st.code("".join(traceback.format_exception(type(exc), exc, exc.__traceback__)), language=None)
        if isinstance(exc, LaunchError) and exc.directory is not None:
            st.caption(f"本次运行记录：{exc.directory}；启动诊断：launch_error.log；后台日志：run.log。")


def _finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def cost_bid_rows(cfg, bids, selected):
    """Compare parameters and offer ranges, never claim offer minus cost is profit."""
    from smm.profit import load_profile, REQUIRED
    from smm.bids import resolve_startup_bid
    profile = load_profile(cfg.root)
    expected_types = {u['id']: u.get('type', family) for family in ('thermal', 'renewable', 'hydro', 'storage', 'pumped') for u in cfg.units.get(family, [])}
    rows = []
    for uid in selected:
        bid = bids['units'][uid]
        cost = profile.get('units', {}).get(uid, {})
        typ = cost.get('type')
        valid = bool(profile.get('available') and typ == expected_types.get(uid) and typ in REQUIRED and
                     all(_finite(cost.get(k)) and cost[k] >= 0 for k in REQUIRED[typ]))
        marginal = next((cost[k] for k in ('scalar_marginal_cost_yuan_per_mwh', 'discharge_degradation_yuan_per_mwh',
                                         'generation_vom_yuan_per_mwh') if valid and k in cost), None)
        for curve, label in CURVE_CN.items():
            values = [s.get('price') for s in bid.get(curve, [])]
            if not values or not all(_finite(v) for v in values):
                continue
            charge = curve in ('ch_segments', 'pump_segments')
            rows.append({'主体': uid, '项目': label, '单位': '元/MWh',
                         '固定核算成本': '购电计入净结算，不重复列成本' if charge else (marginal if marginal is not None else '未知'),
                         '当前报价': f'{min(values):g} ～ {max(values):g}',
                         '对照口径': '购电意愿，不是销售毛利' if charge else '含全部内部报价段；价差不代表利润'})
        if valid and 'fixed_startup_cost_yuan_per_start' in cost:
            resolved = resolve_startup_bid(cfg, bid)
            offer = resolved['cost_yuan'] if resolved['mode'] == 'uniform_per_unit' else '按冷、温、热状态报价'
            rows.append({'主体': uid, '项目': '单次启动', '单位': '元/次', '固定核算成本': cost['fixed_startup_cost_yuan_per_start'],
                         '当前报价': str(offer), '对照口径': '成本与申报值分开维护'})
            rows.append({'主体': uid, '项目': '在线固定项', '单位': '元/h', '固定核算成本': cost['fixed_online_cost_yuan_per_h'],
                         '当前报价': str(bid.get('noload_cost', '未申报')), '对照口径': '成本与申报值分开维护'})
    return rows


def cost_bid_comparison(cfg, bids, selected):
    st.markdown('**固定成本与当前报价对照**')
    st.caption('成本用于利润核算；报价用于出清。以下仅对照参数，不以报价减成本冒充利润；利润还需实际执行量、结算收入及启动/在线费用。')
    try:
        rows = cost_bid_rows(cfg, bids, selected)
        frame = pd.DataFrame(rows)
        # Mixed missing/known values remain visible, never fill unknown costs with zero.
        if not frame.empty:
            frame['固定核算成本'] = frame['固定核算成本'].map(str)
            st.dataframe(frame, hide_index=True, width='stretch')
    except (ValueError, TypeError, KeyError, yaml.YAMLError) as exc:
        business_error('成本对照暂不可用；报价草稿未改变', exc, '请核对运行成本档案及所选主体报价。')


def basis_tables(basis):
    st.markdown('**研究依据与舍入（只读）**')
    st.dataframe(pd.DataFrame([{'主体': c['unit_id'], '曲线': CURVE_CN.get(c['curve'], '其他报价'),
        '舍入前（元/MWh）': str(c['unrounded_prices_yuan_per_mwh']),
        '基础报价（元/MWh）': str(c['rounded_prices_yuan_per_mwh'])} for c in basis.get('changes', [])]), hide_index=True)
    details = [{'主体': c['unit_id'], '参数': BASIS_CN[k], '值': str(v)}
               for c in basis.get('changes', []) for k, v in c.get('basis', {}).items() if k in BASIS_CN]
    if details:
        st.dataframe(pd.DataFrame(details), hide_index=True)
    fixed = [{'主体': uid, '固定成本参数': COST_CN[k], '值': value}
             for uid, d in basis.get('fixed_cost_parameters', {}).items() for k, value in d.items() if k in COST_CN]
    if fixed:
        st.dataframe(pd.DataFrame(fixed), hide_index=True)
    st.caption('以上为合成研究假设，不是真实电厂成本。策略加价不改变固定成本档案；最终候选报价见预览表。')
    with st.expander('技术详情：原始研究档案、来源与文件指纹', expanded=False):
        st.json(basis)


def friendly_diagnostic(message):
    """Retain Chinese field-level limits and numbers; raw diagnosis remains expandable."""
    text = str(message)
    translations = {**CURVE_CN, 'startup_cost_yuan': '单次启动报价', 'startup_cost': '启动报价',
        'noload_cost': '在线固定报价', 'soc_end': '期末荷电状态', 'soc_min': '荷电状态下限',
        'soc_max': '荷电状态上限', 'vup_end': '期末上库库容', 'self_schedule': '自调度曲线',
        'avail_pmax': '申报可用出力', 'p_start': '起点出力', 'p_end': '终点出力', 'price': '价格',
        'cold': '冷态', 'warm': '温态', 'hot': '热态', 'price_quantity': '报量报价',
        'quantity_only': '报量不报价', 'planned': '计划出力', 'dispatch': '调度计划',
        'NaN': '缺失值', 'Infinity': '无穷值'}
    for key in sorted(translations, key=len, reverse=True):
        text = text.replace(key, translations[key])
    if not re.search(r'[一-鿿]', text) or re.search(r'\b(must|requires?|missing|invalid|unknown|expected)\b', text, re.I):
        return '输入格式或数值不符合要求，请核对本项参数（原始原因见技术详情）'
    return text
