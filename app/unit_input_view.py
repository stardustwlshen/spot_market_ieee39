"""Read-only, lossless selected-unit input tables for the simulation workspace."""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import streamlit as st
import yaml

from chinese_labels import VALUES

LABELS = {
    'mode': '申报方式', 'avail_pmax': '可用功率上限（MW）', 'self_schedule': '自调度计划',
    'startup_cost_mode': '启动报价口径', 'startup_cost_source': '启动报价来源',
    'startup_cost': '历史分状态启动报价（元/次）', 'startup_cost_yuan': '单次启动报价（元/次）',
    'noload_cost': '空载报价（元/h）', 'cost_yuan': '费用（元）',
    'hydro_bid': '水电申报参数', 'method': '申报方法', 'season': '适用季节',
    'soc_end': '申报日末荷电状态', 'vup_end': '申报日末上库库容（万m³）',
    'id': '主体编号', 'type': '资源类型', 'bus': '接入节点', 'enabled': '参与模拟',
    'pmax': '最大功率（MW）', 'pmin': '最小技术出力（MW）', 'cap': '装机容量（MW）',
    'profile': '预测曲线', 'schedule_pu_file': '计划曲线', 'ramp': '爬坡率（MW/min；水电为每台）',
    'ut': '最小开机时间（h）', 'dt': '最小停机时间（h）',
    'noload': '技术档案空载参数（元/h，非独立核算成本）',
    'startup': '技术档案启动参数（元/次，非独立核算成本）',
    'cold': '冷态', 'warm': '温态', 'hot': '热态', 'very_hot': '极热态',
    'commitment': '逐台开停约束', 'schema_version': '声明版本', 'initial_source': '初态来源',
    'initial_units': '逐台初态', 'unit_id': '单机编号', 'elapsed_h': '已持续时间（h）',
    'elapsed_kind': '时长口径', 'init': '初始状态', 'online': '在线', 'p0': '初始功率（MW）', 'hours': '已持续时间（h）',
    'regulation': '水电调节类型', 'n_units': '机组台数', 'unit_pmax': '单机最大功率（MW）',
    'zones': '稳定运行区间（MW）', 'water_rate': '耗水率（m³/s/MW）',
    'init_units_on': '初始在线台数', 'init_p_unit': '初始单机功率（MW）',
    'seasons': '季节约束', 'wet': '丰水期', 'dry': '枯水期', 'qin': '来水流量（m³/s）',
    'v0': '初始库容（万m³）', 'vmin': '库容下限（万m³）', 'vmax': '库容上限（万m³）',
    'v_end': '日末库容（万m³）', 'qeco': '生态流量（m³/s）', 'e_day': '日电量上限（MWh）',
    'e_day_min': '日电量下限（MWh）', 'qout_max': '出库流量上限（m³/s）',
    'qout_max_source': '出库流量上限来源', 'upstream': '上游电站', 'lag_periods': '水流滞后时段数',
    'pondage': '调节池容积（万m³）', 'unit_gen_max': '单机最大发电功率（MW）',
    'unit_gen_min': '单机最小发电功率（MW）', 'unit_pump': '单机抽水功率（MW）',
    'variable_speed': '是否变速', 'unit_pump_min': '单机最小抽水功率（MW）',
    'e_gen': '发电库容转换系数（MWh/万m³）', 'e_pump': '抽水库容转换系数（MWh/万m³）',
    'v_total': '上下库总水量（万m³）', 'vup_min': '上库下限（万m³）', 'vup_max': '上库上限（万m³）',
    'vdn_min': '下库下限（万m³）', 'vdn_max': '下库上限（万m³）', 'vup0': '初始上库库容（万m³）',
    'vup_end_bid': '缺省申报日末上库库容（万m³）', 'init_gen': '初始发电台数',
    'init_pump': '初始抽水台数', 'ecap': '能量容量（MWh）', 'eta_c': '充电效率', 'eta_d': '放电效率',
    'soc_min': '荷电状态下限', 'soc_max': '荷电状态上限', 'soc0': '初始荷电状态',
    'soc_end_bid': '缺省申报日末荷电状态',
    'switch_gap': '抽水与发电切换间隔（时段）', 'max_gen_starts': '单台每日发电启动次数上限',
    'max_pump_starts': '单台每日抽水启动次数上限', 'init_mode': '初始工况',
    'start_cost_gen': '技术档案发电启动参数（元/台次，非独立核算成本）',
    'start_cost_pump': '技术档案抽水启动参数（元/台次，非独立核算成本）',
    'scalar_marginal_cost_yuan_per_mwh': '固定边际核算成本（元/MWh）',
    'fixed_online_cost_yuan_per_h': '在线固定核算成本（元/h）',
    'fixed_startup_cost_yuan_per_start': '单次启动核算成本（元/次）',
    'discharge_degradation_yuan_per_mwh': '放电折损核算成本（元/MWh）',
    'generation_vom_yuan_per_mwh': '发电可变运维核算成本（元/MWh）',
    'gen_start_yuan_per_unit_start': '单台发电启动核算成本（元/次）',
    'pump_start_yuan_per_unit_start': '单台抽水启动核算成本（元/次）',
    'fuel_price': '燃料价格（元/GJ）', 'heat_rate': '热耗参数（GJ/MWh，口径见依据）', 'variable_om': '可变运维（元/MWh）',
    'water_opportunity': '水机会成本（元/MWh）', 'replacement_energy': '补能价格（元/MWh）',
    'degradation': '折损成本（元/MWh）', 'reservation': '保留价值（元/MWh）',
    'renewable_vom': '新能源运维依据（元/MWh）',
    'thermal_offer_markup_factors': '火电报价加成倍数', 'renewable_strategy_increments': '新能源报价增量（元/MWh）',
    'hydro_opportunity_increments': '水电机会报价增量（元/MWh）',
    'storage_sell_increments': '储能卖价增量（元/MWh）', 'storage_buy_decrements': '储能买价递减（元/MWh）',
    'pumped_sell_increments': '抽蓄卖价增量（元/MWh）',
    'version': '档案版本', 'source_kind': '来源类型', 'source': '来源说明',
    'accounting_scope': '核算范围', 'currency_energy_units': '货币与能量单位',
    'accounting_warning': '核算边界', 'formula_references': '公式参考来源',
    'no_load_convention': '空载参数约定', 'fixed_cost_link': '固定成本关联说明',
    'thermal_strategy_scope': '火电报价策略范围',
    'thermal_heat_rate_basis': '火电热耗口径', 'thermal_heat_rate_warning': '热耗与空载边界说明',
    'default_bid_model': '默认报价生成方式', 'initial_generation_season': '首次生成水期',
    'season_change_policy': '切换水期的报价保护策略',
    'heat_rate_basis': '热耗口径', 'reference_capacity_mw': '参考容量（MW）',
    'reference_heat_rate': '参考增量热耗（GJ/MWh）', 'reference_variable_om': '参考可变运维（元/MWh）',
    'heat_rate_capacity_exponent': '热耗容量指数（研究假设）', 'vom_capacity_exponent': '运维容量指数（研究假设）',
    'capacity_factor_min': '容量修正下界', 'capacity_factor_max': '容量修正上界',
    'heat_rate_capacity_factor': '热耗容量修正倍率', 'vom_capacity_factor': '运维容量修正倍率',
    'capacity_factor_clamped': '容量修正是否到达边界',
}


VALUE_LABELS = {
    'price_quantity': '报量报价', 'quantity_only': '只报量不报价', 'planned': '水电计划出力',
    'dispatch': '接受调度', 'self_schedule': '自调度计划', 'state_dependent': '按启停状态报价',
    'uniform_per_unit': '每台机组单次启动报价', 'legacy_hot_migration': '沿用该机组历史热态启动报价',
    'explicit_scalar': '显式单次启动报价', 'segments': '分段报价',
    'wet': '丰水期', 'dry': '枯水期', 'reservoir': '库容型', 'run_of_river': '径流式',
    'unit_duration_v1': '逐台最小开停时间', 'legacy_aggregate': '聚合模式',
    'research_assumption': '研究假设（非实测）', 'exact': '声明时长', 'lower_bound': '保守下界',
    'gen': '发电', 'pump': '抽水', 'idle': '空闲',
    'cost_basis_v2': '独立成本与报价策略（新版研究基准）', 'legacy_v1': '历史固定价格模板',
    'incremental_hhv_excludes_separate_online_intercept': '增量高位热耗，不含单列在线截距',
    'legacy_unspecified_not_claimed_average': '历史未明确口径，不解释为平均效率',
}


def _flatten(value, path=()):
    """Emit every leaf, including nulls and empty containers, without rounding."""
    if isinstance(value, dict) and value:
        for key, child in value.items():
            yield from _flatten(child, path + (str(key),))
    elif isinstance(value, (list, tuple)) and value:
        for index, child in enumerate(value):
            yield from _flatten(child, path + (f'[{index}]',))
    else:
        yield path, value


def _value(value):
    if value is None:
        return '未设置（null）'
    if isinstance(value, bool):
        return '是' if value else '否'
    if isinstance(value, str):
        return VALUE_LABELS.get(value, VALUES.get(value, value))
    return str(value)


# Retired non-energy-market display fields may remain in immutable legacy inputs.
_RETIRED_PAYMENT_FIELDS = frozenset({'capacity_price', 'capacity_fee_per_day',
                                     'capacity_payment_yuan', 'capacity_revenue_yuan'})


def field_rows(value):
    return [{'参数': ' / '.join('约束模式' if part == 'mode' and 'commitment' in path else LABELS.get(part, part) for part in path),
             '数值': _value(raw), '原始字段': '.'.join(path)} for path, raw in _flatten(value)
            if not any(part in _RETIRED_PAYMENT_FIELDS for part in path)]


def parameter_rows(value):
    """Business table: retain full paths internally, omit machine keys on screen."""
    return [{key: row[key] for key in ('参数', '数值')} for row in field_rows(value)]


def _read_archive(cfg, filename):
    path = Path(cfg.root) / 'config' / filename
    try:
        if filename == 'fixed_costs.yaml':
            from smm.profit import load_fixed_cost_profile
            data = load_fixed_cost_profile(cfg)
            return (data, None) if data['available'] else (None, '未知：场景未提供可读取的成本档案')
        if filename == 'bid_cost_basis.yaml':
            from smm.bid_cost_basis import research_cost_worksheet
            return research_cost_worksheet(cfg), None
        data = yaml.safe_load(path.read_text(encoding='utf-8'))
        if not isinstance(data, dict):
            return None, '未知：档案不是有效参数表'
        return data, None
    except (OSError, yaml.YAMLError, ValueError, KeyError, TypeError):
        return None, '未知：场景未提供可读取的成本档案'


def unit_information_sections(cfg, uid):
    """Return complete tables scoped to one unit; never modify configuration."""
    matches = [(group, unit) for group, units in cfg.units.items() if isinstance(units, list)
               for unit in units if isinstance(unit, dict) and unit.get('id') == uid]
    if not matches:
        # load_config filters disabled units; their original inputs remain viewable.
        raw, _ = _read_archive(cfg, 'units.yaml')
        matches = [(group, unit) for group, units in (raw or {}).items() if isinstance(units, list)
                   for unit in units if isinstance(unit, dict) and unit.get('id') == uid]
    if len(matches) != 1:
        raise ValueError(f'主体 {uid} 不存在或编号不唯一，不能确定其参数。')
    group, unit = matches[0]
    sections = {}
    buckets = {'主体信息': {}, '技术与运行约束': {}, '初始状态': {}, '季节约束': {}, '技术档案费用参数（不是独立核算成本）': {}}
    for key, value in unit.items():
        category = ('主体信息' if key in ('id', 'type', 'bus', 'enabled', 'profile', 'schedule_pu_file') else
                    '技术档案费用参数（不是独立核算成本）' if key in ('startup', 'noload', 'start_cost_gen', 'start_cost_pump') else
                    '初始状态' if key.startswith('init') or key in ('soc0', 'vup0') else
                    '季节约束' if key == 'seasons' else '技术与运行约束')
        buckets[category][key] = value
    if 'enabled' not in unit:
        buckets['主体信息']['enabled'] = True
    for title, values in buckets.items():
        if values:
            sections[title] = field_rows(values)
    fixed, error = _read_archive(cfg, 'fixed_costs.yaml')
    record = (fixed or {}).get('units', {})
    record = record.get(uid) if isinstance(record, dict) else None
    sections['原成本档案（报价依据，非本次现货核算）'] = field_rows(record) if isinstance(record, dict) and record else [
        {'参数': '主体核算成本', '数值': error or '未知：未提供该主体的独立核算成本', '原始字段': uid}]
    from smm.spot_costs import load_profile as load_spot, parameters as spot_parameters
    from spot_cost_editor import LABELS as SPOT_LABELS
    spot=load_spot(cfg);typ=unit.get('type',group)
    current,reason=spot_parameters(spot,uid,typ)
    sections['现货核算成本（不含运维）'] = ([{'参数':SPOT_LABELS.get(k,LABELS.get(k,k)),'数值':_value(v),'原始字段':k} for k,v in current.items() if k!='derivation'] if current else [{'参数':'成本缺项','数值':reason,'原始字段':uid}])
    if fixed:
        metadata = {key: value for key, value in fixed.items() if key != 'units'}
        if 'unit_sources' in metadata:
            metadata['unit_sources'] = {uid: metadata['unit_sources'][uid]} if uid in metadata['unit_sources'] else {}
            if uid in metadata['unit_sources']:
                metadata['profile_source_kind'] = metadata.get('source_kind')
                metadata['source_kind'] = metadata['unit_sources'][uid]['source_kind']
        if metadata:
            sections['核算成本来源'] = field_rows(metadata)
    basis, error = _read_archive(cfg, 'bid_cost_basis.yaml')
    basis = basis or {}
    unit_section = 'renewable_vom' if group == 'renewable' else group
    records = basis.get(unit_section, {})
    record = records.get(uid) if isinstance(records, dict) else None
    sections['报价生成成本依据（不等于核算成本）'] = (
        field_rows(record if isinstance(record, dict) else {unit_section: record}) if record is not None else
        [{'参数': '主体报价成本依据', '数值': error or '未知：未提供该主体报价成本依据', '原始字段': uid}])
    # Only shared settings for the selected type, plus genuinely common metadata.
    type_keys = {'thermal', 'renewable', 'renewable_vom', 'hydro', 'storage', 'pumped', 'tie', 'units'}
    prefixes = ('thermal_', 'renewable_', 'hydro_', 'storage_', 'pumped_', 'tie_')
    shared = {key: value for key, value in basis.items() if key not in type_keys and
              (key.startswith(group + '_') or not key.startswith(prefixes))}
    if 'unit_sources' in shared:
        shared['unit_sources'] = {uid: shared['unit_sources'][uid]} if uid in shared['unit_sources'] else {}
        if uid in shared['unit_sources']:
            shared['profile_source_kind'] = shared.get('source_kind')
            shared['source_kind'] = shared['unit_sources'][uid]['source_kind']
    if shared:
        sections['报价依据来源与本类策略'] = field_rows(shared)
    return sections


def render_unit_information(cfg, uid, *, compact=False):
    """Render complete Chinese read-only tables for the chosen unit."""
    st.subheader(f'{uid} · 机组信息')
    try:
        sections = unit_information_sections(cfg, uid)
    except ValueError as exc:
        st.error(str(exc))
        return
    if compact:
        rows = [{'分类': title, '参数': row['参数'], '数值': row['数值']}
                for title, values in sections.items() for row in values]
        st.dataframe(pd.DataFrame(rows), hide_index=True, width='stretch', height=560)
        return
    for title, rows in sections.items():
        if title in ('核算成本来源', '报价依据来源与本类策略'):
            # Keep exact provenance in unit_information_sections, but the daily
            # workspace needs only archive identity and actual strategy inputs.
            rows = [row for row in rows if row['原始字段'] in ('version', 'source_kind')
                    or row['原始字段'].split('.')[0].endswith(('_factors', '_increments', '_decrements'))]
            if not rows:
                continue
        st.markdown(f'**{title}**')
        st.dataframe(pd.DataFrame(rows)[['参数', '数值']], hide_index=True, width='stretch')


def scenario_unit_catalog(cfg):
    """Read the scenario's complete catalog, including disabled resources."""
    path = Path(cfg.root) / 'config' / 'units.yaml'
    raw = yaml.safe_load(path.read_text(encoding='utf-8'))
    if not isinstance(raw, dict):
        raise ValueError('场景机组档案不是有效参数表')
    catalog = [(group, unit) for group, units in raw.items() if isinstance(units, list)
               for unit in units if isinstance(unit, dict) and 'id' in unit]
    ids = [unit['id'] for _, unit in catalog]
    if len(ids) != len(set(ids)):
        raise ValueError('场景机组编号重复，无法确定所查看主体')
    # Use the in-memory effective input for enabled resources, retaining disabled
    # records from disk. No filtering or defaults are written back.
    effective = {unit['id']: (group, unit) for group, units in cfg.units.items() if isinstance(units, list)
                 for unit in units if isinstance(unit, dict) and 'id' in unit}
    return [effective.get(unit['id'], (group, unit)) for group, unit in catalog]


def resource_capacity_rows(cfg):
    """Capacity totals split by resource type and enabled status, never summed twice."""
    from smm.fixed_load import tie_mode
    totals = {}
    for group, unit in scenario_unit_catalog(cfg):
        if group == 'tie' and tie_mode(cfg) == 'fixed_load':
            continue  # Fixed demand has no generating capacity.
        kind = unit.get('type', group)
        status = '启用' if unit.get('enabled', True) else '未启用（不参与运行）'
        key = (kind, status)
        row = totals.setdefault(key, {'资源类型': VALUES.get(kind, kind), '状态': status,
                                     '主体数': 0, '发电/放电容量（MW）': 0., '充电/抽水容量（MW）': 0.,
                                     '储能额定能量（MWh）': None, '抽蓄有效库容发电容量（MWh）': None})
        row['主体数'] += 1
        if group == 'hydro':
            output = float(unit['n_units']) * float(unit['unit_pmax'])
        elif group == 'pumped':
            output = float(unit['n_units']) * float(unit['unit_gen_max'])
            row['充电/抽水容量（MW）'] += float(unit['n_units']) * float(unit['unit_pump'])
            # Both reservoirs constrain usable upper-reservoir water.
            lower = max(float(unit['vup_min']), float(unit['v_total']) - float(unit['vdn_max']))
            upper = min(float(unit['vup_max']), float(unit['v_total']) - float(unit['vdn_min']))
            row['抽蓄有效库容发电容量（MWh）'] = (row['抽蓄有效库容发电容量（MWh）'] or 0.) + (upper - lower) * float(unit['e_gen'])
        elif group == 'renewable':
            output = float(unit['cap'])
        else:
            output = float(unit['pmax'])
        row['发电/放电容量（MW）'] += output
        if group == 'storage':
            row['充电/抽水容量（MW）'] += float(unit['pmax'])
            row['储能额定能量（MWh）'] = (row['储能额定能量（MWh）'] or 0.) + float(unit['ecap'])
    return list(totals.values())


def render_network_and_units(cfg, key_prefix='network_inputs', *, bids=None):
    """Read-only pre-run system context and complete peer-unit information."""
    from smm.forecast import generate_profiles, apply_profile_overrides
    from network_view import render_input_network

    st.caption('运行前场景数据，不是市场出清结果；“实际”曲线是本场景模拟输入。')
    render_input_network(cfg, key_prefix + ':topology')

    try:
        rows = resource_capacity_rows(cfg)
        st.markdown('**资源容量概况**')
        st.dataframe(pd.DataFrame(rows), hide_index=True, width='stretch')
    except (OSError, ValueError, KeyError, TypeError, yaml.YAMLError) as exc:
        st.error(f'资源容量暂不能读取，请检查当前场景机组参数：{exc}')

    try:
        profiles = apply_profile_overrides(cfg, generate_profiles(cfg))
        columns = st.columns(2)
        from smm.fixed_load import nodal_demand, tie_mode, tie_profiles
        from smm.network import build_network
        net = build_network(cfg)
        total_actual = sum(nodal_demand(cfg, net, profiles, profiles['load_actual'], stage='actual')[0].values())
        total_da = sum(nodal_demand(cfg, net, profiles, profiles['load_da'])[0].values())
        columns[0].metric('实际负荷峰值（MW）', f"{max(total_actual):,.2f}")
        columns[1].metric('日前预测负荷峰值（MW）', f"{max(total_da):,.2f}")
        st.markdown('**负荷输入曲线**')
        chart = pd.DataFrame({'实际总负荷': total_actual, '日前总负荷': total_da}, index=profiles.time).rename_axis('时间')
        st.line_chart(chart, y_label='功率（MW）')
        if tie_mode(cfg) == 'fixed_load':
            st.caption('总负荷含原节点固定负荷；不报价、不参与需求响应。原负荷与新增部分保留独立曲线，均按冻结基准节点占比分配；新增量逐段守恒。')
            from smm.fixed_load import allocation_weights
            st.dataframe(pd.DataFrame([{'负荷节点': bus, '新增量固定分配权重': weight} for bus, weight in allocation_weights(net).items()]), hide_index=True)
            for unit in cfg.units.get('tie', []):
                st.markdown(f"**{unit['id']} · 转换固定负荷四套总量输入（MW）**")
                st.line_chart(pd.DataFrame(tie_profiles(cfg, profiles, unit), index=profiles.index).set_axis(profiles.time), y_label='功率（MW）')
        else:
            for unit in cfg.units.get('tie', []):
                uid = unit['id']
                st.markdown(f"**{uid} · 固定区外来电计划（MW）**")
                st.caption('区外来电单独计为供给注入，未加到负荷，也不作为可报价负荷。')
                st.line_chart(pd.DataFrame({f'{uid} · 供给注入': profiles[f'{uid}_sched'].to_numpy()},
                                          index=profiles.time), y_label='功率（MW）')
        from renewable_input_view import render_renewable_inputs
        render_renewable_inputs(cfg, profiles, bids=bids, key_prefix=key_prefix + ':renewable')
    except (OSError, ValueError, KeyError, TypeError) as exc:
        st.error(f'负荷与新能源曲线暂不能读取，请修复当前场景预测或覆盖数据：{exc}')

    try:
        catalog = scenario_unit_catalog(cfg)
        if not catalog:
            st.info('当前场景没有可查看的机组。')
            return
        labels = {unit['id']: f"{unit['id']} · {('转换负荷来源（非发电主体）' if group == 'tie' and cfg.base.get('tie_line_mode') == 'fixed_load' else VALUES.get(unit.get('type', group), unit.get('type', group)))}"
                  + ('（未启用）' if not unit.get('enabled', True) else '') for group, unit in catalog}
        uid = st.selectbox('查看其他机组参数', list(labels), format_func=labels.get, key=f'{key_prefix}:unit')
        render_unit_information(cfg, uid)
    except (OSError, ValueError, KeyError, TypeError, yaml.YAMLError) as exc:
        st.error(f'机组目录暂不能读取，请检查当前场景机组档案：{exc}')


def unit_summary_rows(cfg,uid):
    # Small factual summary. Full, lossless parameters stay available.
    group,unit=next((g,u) for g,rows in cfg.units.items() if isinstance(rows,list) for u in rows if isinstance(u,dict) and u.get('id')==uid)
    fields=['bus']
    fields += {'thermal':['pmin','pmax','ramp','ut','dt'],
               'renewable':['cap'],'storage':['pmax','ecap','eta_c','eta_d'],
               'hydro':['n_units','unit_pmax','ramp'],'pumped':['n_units','unit_gen_max','unit_pump']}.get(group,['pmax'])
    return [{'参数':LABELS.get(k,k),'数值':_value(unit[k])} for k in fields if k in unit]


def render_unit_summary(cfg,uid):
    st.caption(' · '.join(row['参数']+'：'+row['数值'] for row in unit_summary_rows(cfg,uid)))


def selected_unit_rows(cfg, uid):
    """Read factual technical and initial-state summaries, including disabled units."""
    group, unit = next((g, u) for g, u in scenario_unit_catalog(cfg) if u['id'] == uid)
    fields = {
        'thermal': ['pmax', 'pmin', 'ramp', 'ut', 'dt'],
        'renewable': ['cap', 'profile'],
        'storage': ['pmax', 'ecap', 'eta_c', 'eta_d', 'soc_min', 'soc_max'],
        'hydro': ['regulation', 'n_units', 'unit_pmax', 'ramp', 'ut', 'dt', 'water_rate', 'zones'],
        'pumped': ['n_units', 'unit_gen_max', 'unit_gen_min', 'unit_pump', 'variable_speed',
                   'e_gen', 'e_pump', 'switch_gap'],
    }.get(group, ['pmax'])
    operation = parameter_rows({key: unit[key] for key in fields if key in unit})
    initial = {}
    if group == 'thermal':
        initial = {'init': unit.get('init', {})}
    elif group == 'hydro':
        season = unit.get('seasons', {}).get(cfg.base['season'], {})
        initial = {key: unit[key] for key in ('init_units_on', 'init_p_unit') if key in unit}
        initial['v0'] = season.get('v0')
        if 'commitment' in unit:
            initial['commitment'] = unit['commitment']
        operation += parameter_rows({'seasons': {cfg.base['season']: {
            key: value for key, value in season.items() if key != 'v0'}}})
    elif group == 'storage':
        initial = {'soc0': unit.get('soc0')}
        initial['日初库存电量（MWh）'] = unit['soc0'] * unit['ecap']
        initial['高于技术SOC下限的可放电量（MWh）'] = (
            unit['soc0'] - unit['soc_min']) * unit['ecap'] * unit['eta_d']
    elif group == 'pumped':
        lower = max(unit['vup_min'], unit['v_total'] - unit['vdn_max'])
        upper = min(unit['vup_max'], unit['v_total'] - unit['vdn_min'])
        operation += parameter_rows({'有效上库下限（万m³）': lower, '有效上库上限（万m³）': upper,
            '有效库容发电容量（MWh）': (upper - lower) * unit['e_gen']})
        mode = unit.get('init_mode', 'idle')
        initial = {'init_mode': mode, 'vup0': unit['vup0'],
                   '日初下库库容（万m³）': unit['v_total'] - unit['vup0'],
                   'init_gen': unit['n_units'] if mode == 'gen' else 0,
                   'init_pump': unit['n_units'] if mode == 'pump' else 0,
                   '日初高于有效下限的可发电量（MWh）': (unit['vup0'] - lower) * unit['e_gen']}
    else:
        initial = {'日初状态': '无独立开停初态；按场景预测曲线和当前申报确定可用出力。'}
    return group, unit, operation, parameter_rows(initial)


def render_selected_unit(cfg, uid):
    """Selected unit first; complete type-specific technical input stays on demand."""
    group, unit, operation, initial = selected_unit_rows(cfg, uid)
    st.markdown('**' + uid + ' · ' + VALUES.get(unit.get('type', group), group) + '**')
    columns = st.columns(2)
    columns[0].metric('接入节点', str(unit['bus']))
    columns[1].metric('参与模拟（已保存）', '是' if unit.get('enabled', True) else '否')
    if not unit.get('enabled', True):
        st.info('该机组不参与下一次出清；以下仍可查看、编辑并保存其技术档案。')
    columns = st.columns(2)
    with columns[0]:
        st.markdown('**关键运行参数**')
        st.dataframe(pd.DataFrame(operation), hide_index=True, width='stretch')
    with columns[1]:
        st.markdown('**日初状态**')
        st.dataframe(pd.DataFrame(initial), hide_index=True, width='stretch')
        st.caption('初态来自场景配置，未核验为实测状态，不自动接续前一天；参与模拟不等于日初开机。')
    if group == 'storage':
        st.caption('日初可放电量按技术SOC下限与放电效率折算；实际出清还受当前报价SOC范围、功率和日末要求约束。')
    if group == 'pumped':
        st.caption('有效库容发电容量是上下库约束共同允许的全部水量区间折算，不是日初可用电量；实际出清还受功率、工况和日末要求约束。')
    with st.expander('完整技术参数（当前机组）'):
        if st.checkbox('查看完整技术参数', key='unit_full_parameters:' + str(cfg.root) + ':' + uid):
            sections = unit_information_sections(cfg, uid)
            for title in ('主体信息', '技术与运行约束', '初始状态', '季节约束', '技术档案费用参数（不是独立核算成本）'):
                if title in sections:
                    st.markdown('**' + title + '**')
                    st.dataframe(pd.DataFrame(sections[title])[['参数', '数值']], hide_index=True, width='stretch')
