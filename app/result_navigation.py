"""Business-first result navigation; only reads the selected run's saved evidence."""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import streamlit as st

MAIN_SECTIONS = ['全网概览', '日前实时对比', '机组及电网运行', '结算与收益', '异常诊断']
OPERATION_SECTIONS = ['机组出力', '储能', '水电与抽蓄', '电网拓扑', '线路负载', '弃风弃光']


def recorded_number(value, format_spec='.1f'):
    """Format saved finite measurements without turning absent values into zero."""
    import math
    try:
        value = float(value)
    except (TypeError, ValueError):
        return '未记录'
    return format(value, format_spec) if math.isfinite(value) else '未记录'


def select_saved_result(results):
    """Remember the run across navigation without changing widget identity."""
    from common import result_label
    keys = [row['dir'] for row in results]
    labels = {row['dir']: result_label(row) for row in results}
    if st.session_state.get('result_sel') not in keys:
        st.session_state['result_sel'] = keys[0]
    # A non-widget key survives leaving this page. The callback updates it
    # before rerun; changing a selectbox index on rerun would reset its identity.
    st.session_state['_result_sel_widget'] = st.session_state['result_sel']
    def remember():
        st.session_state['result_sel'] = st.session_state['_result_sel_widget']
    return st.selectbox('选择结果', keys, format_func=labels.get,
                        key='_result_sel_widget', on_change=remember)


def validate_resource_scope(result):
    """Find whole-resource loss from saved evidence, never today's configuration."""
    da = result['da_unit_awards']
    da_ids = set(da.unit)
    frozen = {}
    root = Path(result['snapshot_root']) if result.get('snapshot_root') else None
    units_file = root / 'config/units.yaml' if root else None
    if units_file is not None and units_file.is_file():
        import yaml
        try:
            catalog = yaml.safe_load(units_file.read_text(encoding='utf-8'))
            base_file = root / 'config/base.yaml'
            base = yaml.safe_load(base_file.read_text(encoding='utf-8')) if base_file.is_file() else None
        except yaml.YAMLError as exc:
            raise ValueError('冻结机组配置无法解析，不能核实主体全集') from exc
        if not isinstance(catalog, dict) or (base is not None and not isinstance(base, dict)):
            raise ValueError('冻结机组配置不是对象记录，不能核实主体全集')
        for group in ('thermal', 'renewable', 'hydro', 'storage', 'pumped', 'tie'):
            rows = catalog.get(group) or []
            if not isinstance(rows, list):
                raise ValueError(f'冻结 {group} 机组清单无效')
            for row in rows:
                if not isinstance(row, dict) or not isinstance(row.get('id'), str) or not row['id'].strip():
                    raise ValueError('冻结机组编号缺失，不能核实主体全集')
                if not row.get('enabled', True):
                    continue  # Disabled resources are not dispatch participants.
                if group == 'tie' and (base is None or base.get('tie_line_mode', 'supply') == 'fixed_load'):
                    continue  # No invented import unit for converted demand or unknown old mode.
                uid = row['id']
                if uid in frozen:
                    raise ValueError('冻结机组编号重复，不能核实主体全集')
                frozen[uid] = row.get('type', group) if group in ('thermal', 'renewable') else group
        missing = set(frozen) - da_ids
        if missing:
            raise ValueError('日前机组出力缺少本次冻结启用主体：' + '、'.join(sorted(missing)))

    def types(table, label):
        if table.type.isna().any() or table.groupby('unit').type.nunique().ne(1).any():
            raise ValueError(f'{label}主体类型缺失或同一主体类型冲突')
        return table.groupby('unit').type.first().to_dict()

    da_types = types(da, '日前')
    if any(da_types[uid] != kind for uid, kind in frozen.items()):
        raise ValueError('日前主体类型与本次冻结机组配置不一致')
    if result.get('full'):
        rt_types = types(result['rt_units'], '实时')
        # Old exports may omit exogenous TIE execution entirely. That existing
        # path remains an explicit warning, not a fabricated zero/DA substitute.
        required_ids = {uid for uid, kind in da_types.items() if kind != 'tie'}
        if not required_ids <= set(rt_types) or not set(rt_types) <= da_ids:
            missing = required_ids - set(rt_types)
            extra = set(rt_types) - da_ids
            raise ValueError('日前与实时出力的主体全集不一致' +
                             (('；实时缺少：' + '、'.join(sorted(missing))) if missing else '') +
                             (('；实时多出：' + '、'.join(sorted(extra))) if extra else ''))
        if any(rt_types[uid] != da_types[uid] for uid in rt_types):
            raise ValueError('日前与实时同一主体类型不一致')

    for kind in ('storage', 'hydro', 'pumped'):
        expected = {uid for uid, typ in da_types.items() if typ == kind}
        for stage in ('da', 'rt') if result.get('full') else ('da',):
            key = stage + '_' + kind
            table = result.get(key)
            if table is None:
                if stage == 'rt' and kind == 'storage' and expected:
                    raise ValueError('实时储能状态缺失，不能核实已保存储能主体')
                continue  # Optional old hydro/pumped/DA-state exports stay unknown.
            identifier = 'station' if kind == 'hydro' else 'unit'
            if not isinstance(table, pd.DataFrame) or identifier not in table:
                raise ValueError(f'{key}.csv 缺少主体编号，不能核实主体全集')
            if set(table[identifier]) != expected:
                raise ValueError(f'{key}.csv 主体全集与本次保存的日前出力不一致')


def validate_display_result(result):
    """Validate saved scope and normalize numeric types, never fill missing rows."""
    for key in ('summary', 'da_summary'):
        if not isinstance(result.get(key), dict):
            raise ValueError('运行摘要不是可读取的对象记录')
        result[key] = dict(result[key])
    def object_field(parent, key, label):
        value = parent.get(key)
        if value is None:
            if key in parent:
                parent[key] = {}
            return {}
        if not isinstance(value, dict):
            raise ValueError(f'{label}应为对象记录，不能读取或视为零')
        return value
    for key in ('spread', 'realtime', 'pumped', 'checks', 'time_s', 'forecast_error',
                'operating_profit', 'reserve', 'regulation', 'da', 'settlement_by_type'):
        object_field(result['summary'], key, f'运行摘要 {key}')
    for key in ('pumped', 'slack'):
        object_field(result['da_summary'], key, f'日前摘要 {key}')
    realtime = result['summary'].get('realtime') or {}
    object_field(realtime, 'tracking_policy', '实时跟踪策略')
    for key in ('summary', 'da_summary'):
        for unit, value in (result[key].get('pumped') or {}).items():
            if not isinstance(value, dict):
                raise ValueError(f'抽蓄摘要 {unit} 应为对象记录')
            if key == 'summary':
                for stage in ('da', 'rt'):
                    object_field(value, stage, f'抽蓄摘要 {unit}.{stage}')
    for kind, value in (result['summary'].get('settlement_by_type') or {}).items():
        if not isinstance(value, dict):
            raise ValueError(f'类型结算摘要 {kind} 应为对象记录')
    required = {
        'da_nodal_prices': ('日前节点电价', {'period', 'time', 'bus', 'lmp', 'lmp_raw', 'energy', 'congestion'}),
        'da_unit_awards': ('日前机组出力', {'period', 'unit', 'type', 'P_mw', 'energy_mwh', 'revenue_yuan'}),
    }
    if result.get('full'):
        required.update({
            'rt_prices': ('实时节点电价', {'period', 'bus', 'lmp'}),
            'rt_units': ('实时机组出力', {'period', 'unit', 'type', 'P_mw'}),
            'rt_system': ('实时系统记录', {'period', 'load_mw'}),
            'settlement_by_type': ('按类型结算', {'type', 'q_rt', 'total', 'da_amount', 'rt_amount', 'dev_pnl'}),
            'settlement_by_entity': ('按主体结算', {'entity', 'type'}),
            'settlement_detail': ('逐时结算', {'entity'}),
            'settlement_reconciliation': ('结算对账', set()),
            'settlement_line_rent': ('线路结算', {'line', 'rent_da', 'rent_rt'}),
        })
        stored_storage = result.get('da_storage')
        if isinstance(stored_storage, pd.DataFrame) and not stored_storage.empty:
            required['rt_storage'] = ('实时储能状态', {'unit', 'period', 'E_mwh'})
    # Mandatory business quantities feed totals, prices and state curves. Optional
    # historical diagnostics (raw-price decomposition, reserve eligibility, etc.)
    # may remain unknown, but invalid execution/settlement quantities must stop.
    finite_fields = {
        'da_nodal_prices': {'lmp'}, 'rt_prices': {'lmp'},
        'da_unit_awards': {'P_mw', 'energy_mwh', 'revenue_yuan'},
        'rt_units': {'P_mw'}, 'rt_system': {'load_mw'},
        'rt_storage': {'E_mwh'},
        'settlement_by_type': {'q_rt', 'total', 'da_amount', 'rt_amount', 'dev_pnl'},
    }
    if isinstance(result.get('da_system'), pd.DataFrame) and not result['da_system'].empty:
        required['da_system'] = ('日前系统记录', {'period', 'load_mw'})
        finite_fields['da_system'] = {'load_mw'}
    for key, (label, columns) in required.items():
        frame = result.get(key)
        if not isinstance(frame, pd.DataFrame) or (frame.empty and key != 'settlement_line_rent'):
            raise ValueError(f'{label}文件缺失或为空（{key}.csv）')
        missing = columns - set(frame)
        if missing:
            raise ValueError(f'{label}缺少必要字段：' + '、'.join(sorted(missing)))
        frame = frame.copy()
        for column in sorted(finite_fields.get(key, set())):
            values = pd.to_numeric(frame[column], errors='coerce')
            if values.isna().any() or values.isin([float('inf'), -float('inf')]).any():
                raise ValueError(f'{label}必要数值字段 {column} 含非数值、缺失或无穷值，不能汇总或视为零')
            frame[column] = values
        if 'period' in frame:
            periods = pd.to_numeric(frame.period, errors='coerce')
            if periods.isna().any() or not ((periods >= 0) & (periods < 96) & (periods % 1 == 0)).all():
                raise ValueError(f'{label}时段编号无效，不能按15分钟读取')
            frame['period'] = periods.astype(int)
            identifier = 'bus' if key.endswith('prices') else 'unit' if key.endswith(('awards', 'units', 'storage')) else None
            if identifier and (frame[identifier].isna().any() or frame[identifier].astype(str).str.strip().eq('').any()):
                raise ValueError(f'{label}的主体/节点编号缺失')
            if identifier and frame.duplicated(['period', identifier]).any():
                raise ValueError(f'{label}的主体/节点与时段记录重复')
            if identifier and frame.groupby(identifier).period.nunique().ne(frame.period.nunique()).any():
                raise ValueError(f'{label}的主体/节点时段记录不完整，不能以缺行当零或完整运行')
            if key in ('da_system', 'rt_system') and frame.period.duplicated().any():
                raise ValueError(f'{label}时段记录重复')
        result[key] = frame

    validate_resource_scope(result)

    da_count = result['da_summary'].get('expected_periods')
    if da_count is None:
        da_count = result['summary'].get('expected_periods')
    rt_count = result['summary'].get('executed_periods')
    if rt_count is None:
        rt_count = result['summary'].get('periods')  # Explicit count in older full archives.
    for stage, price_key, keys, count in (
        ('日前', 'da_nodal_prices', ('da_unit_awards', 'da_system'), da_count),
        ('实时', 'rt_prices', ('rt_units', 'rt_system', 'rt_storage'), rt_count),
    ):
        if stage == '实时' and not result.get('full'):
            continue
        periods = set(result[price_key].period)
        if periods != set(range(max(periods) + 1)):
            raise ValueError(f'{stage}时段记录不连续或不是从第1段开始，不能冒充完整执行范围')
        if count is not None:
            try:
                numeric_count = float(count)
            except (TypeError, ValueError):
                numeric_count = float('nan')
            if isinstance(count, bool) or not (0 < numeric_count <= 96 and numeric_count.is_integer()):
                raise ValueError(f'{stage}摘要时段数量无效')
            if periods != set(range(int(numeric_count))):
                raise ValueError(f'{stage}实际记录的时段与摘要声明的 {int(numeric_count)} 段不一致')
        for key in keys:
            table = result.get(key)
            if isinstance(table, pd.DataFrame) and not table.empty and set(table.period) != periods:
                raise ValueError(f'{stage}各结果表的时段范围不一致（{key}.csv）')
    if result.get('full') and set(result['rt_prices'].bus) != set(result['da_nodal_prices'].bus):
        raise ValueError('日前与实时节点电价的节点集合不一致，不能以缺失节点冒充全网结果')
    frozen_root = result.get('snapshot_root')
    frozen_buses = Path(frozen_root) / 'data/case39/buses.csv' if frozen_root else None
    if frozen_buses is not None and frozen_buses.is_file():
        buses = pd.read_csv(frozen_buses)
        if 'bus' not in buses or buses.bus.isna().any() or buses.bus.duplicated().any():
            raise ValueError('冻结节点编号缺失或重复，不能核实全网节点范围')
        if set(result['da_nodal_prices'].bus) != set(buses.bus):
            raise ValueError('日前节点电价与本次冻结网络的节点集合不一致，不能以缺失节点冒充全网结果')


def comparison_frame(result):
    """Align saved periods without extrapolating a partial RT run or filling gaps."""
    frames = []
    for stage, prefix in [('da', '日前'), ('rt', '实时已执行')]:
        system = result.get(stage+'_system')
        if system is None or system.empty or not {'period', 'load_mw'} <= set(system):
            continue
        if system.period.duplicated().any():
            raise ValueError('系统结果时段重复，不能汇总为日前实时对比')
        columns = [c for c in ('period', 'load_mw', 'shed_mw') if c in system]
        frame = system[columns].copy().set_index('period')
        frame = frame.rename(columns={'load_mw':prefix+'负荷 MW', 'shed_mw':prefix+'切负荷 MW'})
        power = result.get('da_unit_awards' if stage == 'da' else 'rt_units')
        if power is not None and {'period', 'P_mw', 'unit'} <= set(power):
            if power.duplicated(['unit', 'period']).any():
                raise ValueError('机组结果时段重复，不能汇总为日前实时对比')
            # Gross supply minus charging/pumping, including only archived TIE rows.
            if power.unit.isna().any():
                raise ValueError('机组结果主体标识缺失，不能汇总为日前实时对比')
            quantities = power[['period', 'P_mw']].copy()
            quantities['P_mw'] = pd.to_numeric(quantities.P_mw, errors='coerce').replace([float('inf'), -float('inf')], float('nan'))
            # Missing values or a missing saved unit row make the total unknown.
            unit_count = int(power.unit.nunique())
            frame[prefix+'保存净供给 MW'] = quantities.groupby('period').P_mw.sum(min_count=unit_count)
        frames.append(frame)
    return pd.concat(frames, axis=1).sort_index().reset_index() if frames else pd.DataFrame()


def build_result_navigation(summary, da_summary, *, directory):
    from market_views import result_health_dashboard, result_quality_messages
    from output_browser import render_output_browser
    messages = result_quality_messages(summary, da_summary, directory=directory)
    important = [(level, text) for level, text in messages if level in ('error', 'warning')]
    if important:
        render = st.error if any(level == 'error' for level, _ in important) else st.warning
        render('本次运行有需要注意的结果：\n\n' + '\n\n'.join('• '+text.split('。')[0]+'。' for _, text in important) + '\n\n完整检查范围与原因见“异常诊断”。')
    st.caption('阅读顺序：先看全网概况，再比日前与实时，随后检查机组和电网，最后查看结算收益及异常原因。缺失记录不按零处理。')
    main = st.tabs(MAIN_SECTIONS)
    with main[2]:
        operation = st.tabs(OPERATION_SECTIONS)
    with main[4]:
        diagnostic = st.tabs(['未满足项与求解质量', '主体与求解阶段', '完整存档与来源'])
    with diagnostic[0]:
        result_health_dashboard(summary, da_summary, directory=directory, messages=messages)
        advanced = st.expander('高级诊断与技术记录（按需查看）', expanded=False)
        with advanced:
            if da_summary.get('pricing_source') == 'bounded_auxiliary_pricing':
                st.caption('物理出力与状态来自正式调度；结算价采用独立辅助定价并经市场限价。辅助模型数量不执行。')
            gap = da_summary.get('scuc_gap')
            st.caption('日前混合整数相对间隙：'+('未记录' if gap is None else f'{gap:.2%}'))
    with diagnostic[2]:
        render_output_browser(directory)
    return dict(main=main, operation=operation, diagnostic=diagnostic, advanced=advanced)
