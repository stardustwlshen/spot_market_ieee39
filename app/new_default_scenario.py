"""Explicitly opt into repository defaults without migrating existing scenes."""
from __future__ import annotations

import streamlit as st
import yaml

from smm import scenario as sc
from smm.config import load_config


def _summary(cfg) -> str:
    renewable = cfg.units.get('renewable', [])
    pv = sum(float(u['cap']) for u in renewable if u.get('type') == 'pv')
    wind = sum(float(u['cap']) for u in renewable if u.get('type') == 'wind')
    storage = cfg.units.get('storage', [])
    battery_mw = sum(float(u['pmax']) for u in storage)
    battery_mwh = sum(float(u['ecap']) for u in storage)
    forecast = cfg.base.get('forecast', {}).get('model', 'legacy_v1')
    tie = '区外来电注入' if cfg.base.get('tie_line_mode', 'supply') == 'supply' else '原联络线转负荷'
    return f'光伏 {pv:g} MW · 风电 {wind:g} MW · 电池 {battery_mw:g} MW / {battery_mwh:g} MWh · {forecast} · {tie}'


def render_new_default_scene(current_name: str, *, key: str, disabled: bool = False) -> None:
    """Called inside the selector's chosen container; creation requires a click."""
    with st.expander('从新版默认配置新建场景（保留旧场景）'):
        st.caption('使用此项目当前默认机组、网络、曲线模型、成本与报价，另建独立场景。不会复制当前场景的手工覆盖曲线或改价，也不会覆盖旧场景与历史结果。')
        try:
            fresh = load_config(root=sc.ROOT)
            st.caption('新版默认：' + _summary(fresh))
        except (OSError, ValueError, TypeError, KeyError, AttributeError, yaml.YAMLError) as exc:
            st.error('项目默认配置暂不能读取，请修复后再新建。')
            st.caption(str(exc))
            return
        try:
            st.caption('当前场景：' + _summary(load_config(root=sc.scenario_path(current_name))))
        except (OSError, ValueError, TypeError, KeyError, AttributeError, yaml.YAMLError):
            st.caption('当前场景配置不能读取；仍可另建新版场景，原文件保留。')
        st.caption('以上为装机与输入模型，不是实际发电比例或本次出清结果。')
        needs_restart = bool(st.session_state.get('runtime_needs_restart'))
        disabled = disabled or needs_restart
        if needs_restart:
            st.info('请先重启应用，再从新版默认配置新建，避免混用更新前后的代码。')
        with st.form(f'new_default:{key}'):
            name = st.text_input('新版场景名称', value='新版高光伏', key=f'new_default:{key}:name')
            create = st.form_submit_button('创建并切换到新版场景', disabled=disabled)
        if not create or disabled:
            return
        try:
            path = sc.create_scenario(name.strip(), source=None,
                                      note='用户明确从项目当前默认配置新建；旧场景与历史结果保留。')
        except (OSError, ValueError, TypeError, KeyError, RuntimeError) as exc:
            st.error(f'未创建新版场景：{exc}。已有场景不会被覆盖。')
            return
        st.session_state['new_default_switch_to'] = path.name
        st.session_state['new_default_created_notice'] = f'已创建并切换到“{path.name}”。采用项目当前默认输入与报价；旧场景、手工曲线、改价和历史结果保留。'
        st.rerun()
