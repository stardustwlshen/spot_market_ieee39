"""Small two-step editor; forms never persist until explicit validated Save."""
import copy

import pandas as pd
import streamlit as st

from unit_input_view import LABELS, parameter_rows
from unit_parameter_help import field_help, field_readonly, effective_quote_rows
from smm.network import build_network
from smm.unit_editor import TYPES, catalog, scene_fingerprint, unit_template, prepare_unit_edit, apply_unit_edit

_LABELS = {**LABELS, 'enabled': '参与模拟', 'profile': '预测曲线类型', 'pondage': '调节池容积（万m³）'}
_ENUMS = {'profile': ['wind_a', 'wind_b', 'pv'], 'regulation': ['reservoir', 'run_of_river'],
          'init_mode': ['idle', 'gen', 'pump']}
_NAMES = {'wind_a': '风电曲线A', 'wind_b': '风电曲线B', 'pv': '光伏日照曲线',
          'reservoir': '水库式', 'run_of_river': '径流式', 'idle': '停机', 'gen': '发电', 'pump': '抽水'}
_PRIMARY = {'thermal': ['pmax', 'pmin', 'ramp'], 'renewable': ['cap', 'profile'],
            'storage': ['pmax', 'ecap', 'soc0'], 'hydro': ['n_units', 'unit_pmax', 'water_rate'],
            'pumped': ['n_units', 'unit_gen_max', 'unit_pump']}


def _field(value, path, prefix, *, disabled=False, family=None, unit=None):
    unit = unit or {}
    name = path[-1]
    label = ' / '.join(_LABELS.get(x, x) for x in path)
    key = prefix + ':' + '.'.join(path)
    if isinstance(value, dict):
        return {k: _field(v, path + (k,), prefix, disabled=disabled, family=family, unit=unit) for k, v in value.items()}
    disabled = disabled or field_readonly(family, path, unit)
    help_text = field_help(family, path, unit)
    if name == 'zones':
        frame = pd.DataFrame(value, columns=['下限（MW）', '上限（MW）'])
        st.caption(label, help=help_text)
        result = st.data_editor(frame, hide_index=True, num_rows='dynamic', key=key, disabled=disabled)
        return result.values.tolist()
    if isinstance(value, bool):
        return st.checkbox(label, value=value, key=key, disabled=disabled, help=help_text)
    if name in _ENUMS:
        options = list(_ENUMS[name])
        if value not in options:
            options = [value, *options]  # Preserve invalid legacy input until deliberately repaired.
        return st.selectbox(label, options, index=options.index(value), key=key,
                            format_func=lambda x: _NAMES.get(x, x), disabled=disabled, help=help_text)
    if name == 'upstream':
        value = st.text_input(label + '（留空表示无上游）', value=value or '', key=key, disabled=disabled, help=help_text)
        return value.strip() or None
    if value is None:
        active = st.checkbox('设置' + label + '（不选表示不设置）', value=False, key=key+':set', disabled=disabled, help=help_text)
        number = st.number_input(label, value=0., key=key+':number', disabled=disabled, help=help_text)
        return number if active else None
    if isinstance(value, (float, int)):
        if name in ('ut', 'dt', 'hours'):
            value = float(value)  # Hour durations are not integer counters.
        return st.number_input(label, value=value, key=key, disabled=disabled, help=help_text)
    edited = st.text_input(label, value=str(value), key=key, disabled=disabled, help=help_text)
    return value if field_readonly(family, path, unit) else edited


def _hydro_commitment_editor(candidate, prefix, disabled, active):
    from smm.hydro_commitment import research_initial
    previous = candidate.get('commitment', {})
    if not active:
        candidate['commitment'] = {**previous, 'mode': 'legacy_aggregate'}
        st.caption('旧模式不约束水电最小开停时间。')
        return
    declaration = copy.deepcopy(previous) if previous.get('initial_units') else research_initial(candidate)
    st.caption('逐台研究初态，可修改；不是设备实测。默认已持续1小时，修改最小时长不会自动改此值。')
    frame = pd.DataFrame(declaration['initial_units'])
    edited = st.data_editor(frame, hide_index=True, num_rows='dynamic', key=prefix+':hydro_initial_units',
                           disabled=disabled, column_config={
        'unit_id': st.column_config.TextColumn('机组编号'),
        'online': st.column_config.CheckboxColumn('在线'),
        'elapsed_h': st.column_config.NumberColumn('已持续时间（h）', min_value=0.),
        'elapsed_kind': st.column_config.SelectboxColumn('时长来源',options=['exact','lower_bound'],
            help='exact为声明的模拟时长；lower_bound为保守下界，未知历史可明确填下界0。')})
    rows = edited.to_dict('records')
    candidate['commitment'] = dict(schema_version=1, mode='unit_duration_v1',
                                  initial_source='research_assumption', initial_units=rows)
    candidate['init_units_on'] = sum(r.get('online') is True for r in rows)
    st.caption('修改后作为本次单日模拟的研究初态；已保存运行快照保持不变。')


def _forget_quote_widgets(name):
    for key in list(st.session_state):
        if ((key == 'quote_current:' + name or key.startswith('quote_current:' + name + ':')) or key.startswith('quote_edit:quote_current:' + name + ':')
                or key.startswith('quote_preview:quote_current:' + name + ':')):
            del st.session_state[key]


def render_unit_editor(cfg, name, *, disabled=False, compact=False, selected_uid=None):
    session_key = 'unit_editor:' + name
    notice = st.session_state.pop(session_key + ':notice', None)
    if notice:
        st.success(notice)
    with st.expander('编辑或新增机组', expanded=session_key in st.session_state):
        st.caption('只编辑当前场景；取消不保存。新增默认值为模拟假设。')
        if session_key not in st.session_state:
            action = st.radio('机组操作', ['编辑现有机组', '新增机组', '复制机组'], horizontal=True,
                              key=session_key+':action', disabled=disabled)
            entries = catalog(cfg.root)
            source = None
            if action != '新增机组':
                labels = {u['id']: (g, u) for g, u in entries}
                if not labels:
                    st.info('当前没有可编辑机组，请选择新增机组。')
                    return
                if selected_uid in labels:
                    selected = selected_uid
                    st.caption(('复制来源：' if action == '复制机组' else '当前编辑：') + selected)
                else:
                    from unit_selection import selection_key, _remember
                    shared = st.session_state.get(selection_key(name))
                    source_key = session_key+':source'
                    if shared in labels and st.session_state.get(source_key) != shared:
                        st.session_state[source_key] = shared
                    selected = st.selectbox('机组模板' if action == '复制机组' else '要编辑的机组',
                        list(labels), key=source_key, disabled=disabled,
                        on_change=_remember, args=(name, source_key))
                family, source = labels[selected]
            else:
                kind = st.selectbox('新增类型', list(TYPES), format_func=lambda x: TYPES[x][1],
                                    key=session_key+':type', disabled=disabled)
                family = TYPES[kind][0]
            if action != '编辑现有机组':
                uid = st.text_input('新机组编号（不能重复）', value=(source['id']+'_副本' if source else 'NEW'),
                                    key=session_key+':id:'+(source['id'] if source else kind), disabled=disabled)
            buses = build_network(cfg).buses
            initial_bus = source['bus'] if source else buses[0]
            bus_options = buses if initial_bus in buses else [initial_bus, *buses]
            bus = initial_bus
            if not compact or action != '编辑现有机组':
                bus = st.selectbox('接入节点', bus_options, index=bus_options.index(initial_bus),
                                   key=session_key+':bus:'+action+':'+(source['id'] if source else kind), disabled=disabled)
            if action == '新增机组':
                capacity = st.number_input('总装机功率（MW）', value=100., min_value=.001, key=session_key+':capacity:'+kind, disabled=disabled)
                count = st.number_input('机组台数', value=1, min_value=1, key=session_key+':count:'+kind, disabled=disabled) if family in ('hydro', 'pumped') else 1
                energy = st.number_input('电池能量容量（MWh）', value=200., min_value=.001,
                                         key=session_key+':energy', disabled=disabled) if family == 'storage' else 200.
                st.caption('下一步核对初态、效率、爬坡与库容等参数。')
            if st.button('核对技术参数', key=session_key+':open', disabled=disabled):
                try:
                    unit = copy.deepcopy(source) if source else unit_template(kind, uid, bus, capacity, n_units=count, energy=energy)
                    unit['bus'] = bus
                    if action == '复制机组':
                        unit['id'] = uid
                    st.session_state[session_key] = dict(unit=unit, family=family,
                        original_id=source['id'] if action == '编辑现有机组' else None,
                        fingerprint=scene_fingerprint(cfg.root), token=st.session_state.get(session_key+':token', 0),
                        action=action, source_id=source['id'] if source else None)
                    st.rerun()
                except (ValueError, TypeError, OSError, RuntimeError) as exc:
                    st.error('未进入编辑：' + str(exc))
            st.caption('固定联络线按外部计划处理。')
            return
        editor = st.session_state[session_key]
        original = editor['unit']
        family = editor['family']
        st.markdown('**' + original['id'] + ' · 核对后保存**')
        st.caption('保留手工报价与自定义成本；参数检查不代表全时段可行。')
        rows = effective_quote_rows(cfg, original, family)
        if rows:
            with st.expander('当前生效值与来源'):
                st.dataframe(pd.DataFrame(rows), hide_index=True, width='stretch')
                st.caption('当前申报在上方报价区修改；技术缺省值不会覆盖已有报价。')
        prefix = session_key+':fields:'+str(editor['token'])
        hydro_enabled = False
        if family == 'hydro':
            hydro_enabled = st.checkbox('启用逐台最小开停时间',
                value=original.get('commitment', {}).get('mode') == 'unit_duration_v1',
                key=prefix+':hydro_commitment_enabled', disabled=disabled,
                help='每台分别约束最小开停时间；跨午夜未完成部分继续保留。此处只改草稿，保存后生效。')
        with st.form(session_key+':form'):
            candidate = copy.deepcopy(original)
            candidate['enabled'] = st.checkbox('参与模拟', value=original.get('enabled', True),
                key=prefix+':enabled', disabled=disabled,
                help='勾选表示参与全网出清；与当前选中机组、日初开停状态独立。只在保存机组后生效。')
            st.caption('参与模拟决定是否进入出清；日初开停表示模拟开始前的状态。取消不改变任何设置。')
            buses = build_network(cfg).buses
            bus_options = buses if original['bus'] in buses else [original['bus'], *buses]
            candidate['bus'] = st.selectbox('接入节点', bus_options,
                index=bus_options.index(original['bus']), key=prefix+':bus', disabled=disabled)
            for field in _PRIMARY[family]:
                candidate[field] = _field(original[field], (field,), prefix, disabled=disabled, family=family, unit=original)
            if family == 'hydro':
                _hydro_commitment_editor(candidate, prefix, disabled, hydro_enabled)
            with st.expander('其余技术参数与初始状态（可修改）'):
                for field, value in original.items():
                    if field not in {'id', 'type', 'bus', 'enabled', 'commitment', *_PRIMARY[family]}:
                        if family == 'hydro' and field == 'init_units_on' and candidate.get('commitment', {}).get('mode') == 'unit_duration_v1':
                            st.caption(f"初始在线台数：{candidate['init_units_on']}（由逐台状态计算）")
                            continue
                        candidate[field] = _field(candidate.get(field, value), (field,), prefix, disabled=disabled, family=family, unit=candidate)
            reset_bid = False
            if editor['original_id'] is not None:
                reset_bid = st.checkbox('仅为此机组重新生成模拟报价（旧报价完整备份）',
                                        key=prefix+':reset_bid', disabled=disabled)
                st.caption('报价不兼容时拒绝保存；勾选后仅重建本机组报价。')
            else:
                st.caption('新增机组生成模拟成本与报价；复制不沿用原机组的真实成本。')
            check = st.form_submit_button('检查参数', disabled=disabled)
            save = st.form_submit_button('保存机组', type='primary', disabled=disabled)
            cancel = st.form_submit_button('取消编辑')
        if cancel:
            st.session_state[session_key+':action'] = editor['action']
            if editor['source_id'] is not None:
                st.session_state[session_key+':source'] = editor['source_id']
            del st.session_state[session_key]
            st.session_state[session_key+':token'] = editor['token']+1
            st.rerun()
        if check or save:
            try:
                plan = prepare_unit_edit(cfg.root, family, candidate, original_id=editor['original_id'],
                                         reset_bid=reset_bid, expected=editor['fingerprint'])
                if save:
                    apply_unit_edit(plan)
                    _forget_quote_widgets(name)
                    from unit_selection import select_unit
                    select_unit(st.session_state, name, candidate['id'])
                    st.session_state.pop(session_key, None)
                    st.session_state[session_key+':token'] = editor['token']+1
                    st.session_state[session_key+':notice'] = f"已保存 {candidate['id']} 到场景“{name}”。历史结果未改变。"
                    st.rerun()
                else:
                    st.success('参数、节点、当前报价及出清输入检查通过。点击“保存机组”才会修改当前场景。')
                    st.dataframe(pd.DataFrame(parameter_rows(candidate)), hide_index=True, width='stretch')
                    st.caption('本机组成本：' + ('按当前参数更新模拟假设' if plan['generated_cost'] else '保留现有真实/自定义记录或停用机组成本'))
                    for warning in plan['warnings']:
                        st.warning(warning)
            except (ValueError, KeyError, TypeError, OSError, RuntimeError) as exc:
                st.error('未保存，原场景保持不变：' + str(exc))
