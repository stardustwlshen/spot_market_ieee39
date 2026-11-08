"""Session-only focus shared by unit details, editing and quotations.

Selection never changes participation, input files, or the set of market bids.
"""
import streamlit as st

from chinese_labels import VALUES
from unit_input_view import scenario_unit_catalog


def selection_key(name):
    return 'selected_unit:' + name


def select_unit(state, name, uid):
    state[selection_key(name)] = uid


def _remember(name, widget_key):
    select_unit(st.session_state, name, st.session_state[widget_key])


def render_unit_selector(cfg, name, *, quote=False, disabled=False):
    entries = [(g, u) for g, u in scenario_unit_catalog(cfg) if g != 'tie']
    if not entries:
        st.info('当前没有可选择的机组；可在下方新增。')
        return None
    records = {u['id']: (g, u) for g, u in entries}
    current = st.session_state.get(selection_key(name), st.session_state.get('quote_unit:' + name))
    if current not in records:
        current = next(iter(records))
    # Widget keys are disposable across pages; the selected unit is not.
    if not quote:
        types = list(dict.fromkeys(u.get('type', g) for g, u in entries))
        filter_key = 'unit_type_filter:' + name
        selected_type = records[current][1].get('type', records[current][0])
        pending = st.session_state.get(selection_key(name)) != st.session_state.get('unit_last_rendered:' + name)
        if pending and st.session_state.get(filter_key, 'all') not in ('all', selected_type):
            st.session_state[filter_key] = 'all'
        kind = st.selectbox('机组类型', ['all', *types], key=filter_key,
                            format_func=lambda x: '全部类型' if x == 'all' else VALUES.get(x, x),
                            disabled=disabled)
        records = {uid: pair for uid, pair in records.items()
                   if kind == 'all' or pair[1].get('type', pair[0]) == kind}
        if current not in records:
            current = next(iter(records))
    key = ('quote_unit:' if quote else 'unit_selected_widget:') + name
    if st.session_state.get(key) != current:
        st.session_state[key] = current
    def label(uid):
        group, unit = records[uid]
        return uid + ' · ' + VALUES.get(unit.get('type', group), group) + (
            '（不参与模拟）' if not unit.get('enabled', True) else '')
    uid = st.selectbox('模拟机组' if quote else '当前机组', list(records), key=key,
                      format_func=label, disabled=disabled,
                      on_change=_remember, args=(name, key),
                      help='只选择当前查看或编辑的机组，不改变其他机组是否参与全网出清。')
    select_unit(st.session_state, name, uid)
    if not quote:
        st.session_state['unit_last_rendered:' + name] = uid
    return uid
