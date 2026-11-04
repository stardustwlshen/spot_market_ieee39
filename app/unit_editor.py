"""Small two-step editor; forms never persist until explicit validated Save."""
import copy

import pandas as pd
import streamlit as st

from unit_input_view import LABELS, parameter_rows
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


def _field(value, path, prefix, *, disabled=False):
    name = path[-1]
    label = ' / '.join(_LABELS.get(x, x) for x in path)
    key = prefix + ':' + '.'.join(path)
    if isinstance(value, dict):
        return {k: _field(v, path + (k,), prefix, disabled=disabled) for k, v in value.items()}
    if name == 'zones':
        frame = pd.DataFrame(value, columns=['下限（MW）', '上限（MW）'])
        st.caption(label)
        result = st.data_editor(frame, hide_index=True, num_rows='dynamic', key=key, disabled=disabled)
        return result.values.tolist()
    if isinstance(value, bool):
        return st.checkbox(label, value=value, key=key, disabled=disabled)
    if name in _ENUMS:
        options = list(_ENUMS[name])
        if value not in options:
            options = [value, *options]  # Preserve invalid legacy input until deliberately repaired.
        return st.selectbox(label, options, index=options.index(value), key=key,
                            format_func=lambda x: _NAMES.get(x, x), disabled=disabled)
    if name == 'upstream':
        value = st.text_input(label + '（留空表示无上游）', value=value or '', key=key, disabled=disabled)
        return value.strip() or None
    if value is None:
        active = st.checkbox('设置' + label + '（不选表示不设置）', value=False, key=key+':set', disabled=disabled)
        number = st.number_input(label, value=0., key=key+':number', disabled=disabled)
        return number if active else None
    if isinstance(value, (float, int)):
        return st.number_input(label, value=value, key=key, disabled=disabled)
    return st.text_input(label, value=str(value), key=key, disabled=disabled)


def _forget_quote_widgets(name):
    for key in list(st.session_state):
        if ((key == 'quote_current:' + name or key.startswith('quote_current:' + name + ':')) or key.startswith('quote_edit:quote_current:' + name + ':')
                or key.startswith('quote_preview:quote_current:' + name + ':')):
            del st.session_state[key]


def render_unit_editor(cfg, name, *, disabled=False):
    session_key = 'unit_editor:' + name
    notice = st.session_state.pop(session_key + ':notice', None)
    if notice:
        st.success(notice)
    with st.expander('编辑或新增机组', expanded=session_key in st.session_state):
        st.caption('只作用于当前场景。历史运行与原报价版本保持不变；取消不保存。所有自动参数、成本和报价均为模拟假设，不是实测数据。')
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
                selected = st.selectbox('机组模板' if action == '复制机组' else '要编辑的机组',
                    list(labels), key=session_key+':source', disabled=disabled)
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
            bus = st.selectbox('接入节点', bus_options, index=bus_options.index(initial_bus),
                               key=session_key+':bus:'+action+':'+(source['id'] if source else kind), disabled=disabled)
            if action == '新增机组':
                capacity = st.number_input('总装机功率（MW）', value=100., min_value=.001, key=session_key+':capacity:'+kind, disabled=disabled)
                count = st.number_input('机组台数', value=1, min_value=1, key=session_key+':count:'+kind, disabled=disabled) if family in ('hydro', 'pumped') else 1
                energy = st.number_input('电池能量容量（MWh）', value=200., min_value=.001,
                                         key=session_key+':energy', disabled=disabled) if family == 'storage' else 200.
                st.caption('先填编号、节点和容量即可。下一步可查看、更改初态、效率、爬坡、启停、水文与库容等完整默认值。')
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
            st.caption('固定联络线是外部计划，不作为新发电机组新增；其负荷口径使用下方场景设置。')
            return
        editor = st.session_state[session_key]
        original = editor['unit']
        family = editor['family']
        st.markdown('**' + original['id'] + ' · 核对后保存**')
        st.caption('成本仅更新本机组可确认的模拟记录；真实或自定义成本保持不变。编辑容量不会暗中缩放当前报价。输入检查不等于全时段可行性证明，运行仍会校核供需与约束。')
        prefix = session_key+':fields:'+str(editor['token'])
        with st.form(session_key+':form'):
            candidate = copy.deepcopy(original)
            buses = build_network(cfg).buses
            bus_options = buses if original['bus'] in buses else [original['bus'], *buses]
            candidate['bus'] = st.selectbox('接入节点', bus_options,
                index=bus_options.index(original['bus']), key=prefix+':bus', disabled=disabled)
            for field in _PRIMARY[family]:
                candidate[field] = _field(original[field], (field,), prefix, disabled=disabled)
            with st.expander('其余技术参数与初始状态（可修改）'):
                for field, value in original.items():
                    if field not in {'id', 'type', 'bus', *_PRIMARY[family]}:
                        candidate[field] = _field(value, (field,), prefix, disabled=disabled)
            reset_bid = False
            if editor['original_id'] is not None:
                reset_bid = st.checkbox('仅为此机组重新生成模拟报价（旧报价完整备份）',
                                        key=prefix+':reset_bid', disabled=disabled)
                st.caption('默认保留原报价。容量、SOC或库容改动如使报价不兼容，保存会明确报错；勾选后只重建此机组。')
            else:
                st.caption('启用的新增机组将同时生成对应模拟成本依据和当前报价，其他机组报价不变。复制模板仅复制技术参数，不复制原机组的真实成本。')
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
