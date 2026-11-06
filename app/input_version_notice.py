"""Fresh scene/run provenance status, independent of result data caches."""
from pathlib import Path

import streamlit as st

from smm.input_version import compare_source_version


def render_input_version_notice(meta):
    if meta.get('series_root'):
        from smm.series_ui_adapter import series_source_status
        status=series_source_status(meta)
        {'equal':st.success,'changed':st.warning,'unknown':st.info}[status['status']](status['message'])
        with st.expander('该日来源与接续凭据',expanded=False):st.text(status['detail'])
        return status
    from common import sc
    name = meta.get('scenario')
    try:
        root = sc.scenario_path(name) if name and name != '命令行outputs' else None
    except ValueError:
        root = None
    options = st.session_state.get('network_run_options:' + str(name))
    status = compare_source_version(meta['dir'], root, options=options)
    {'equal': st.success, 'changed': st.warning, 'unknown': st.info}[status['status']](status['message'])
    if status.get('detail'):
        with st.expander('来源核对详情', expanded=False):
            st.code(status['detail'])
    if status['changes']:
        st.caption('变化项：' + '、'.join(status['changes'][:8]) + (' 等' if len(status['changes']) > 8 else ''))
    if status['status'] == 'changed':
        st.page_link('pages/2_运行.py', label='按当前输入运行：前往运行页确认后启动', icon='▶️')
    st.caption(f"历史运行：{Path(meta['dir']).name} · 场景：{name or '未记录'}；只读冻结输入，不自动重新求解。")
    return status
