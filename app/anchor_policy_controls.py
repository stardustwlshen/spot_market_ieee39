"""Explicit scenario migration; never rewrite historical frozen runs."""
from copy import deepcopy

from smm.storage_anchors import validate_anchor_config


def two_hour_modes(cfg, modes):
    """Return a validated copy changing only the storage/pumped anchor policy."""
    from dataclasses import replace
    updated = deepcopy(modes)
    storage = updated[cfg.rule_set]['realtime']['storage']
    storage.update(mode='sced_anchor', anchor='two_hour_eq')
    validate_anchor_config(replace(cfg, mode=updated[cfg.rule_set]))
    return updated


def render_anchor_policy(cfg, scenario, disabled=False):
    import streamlit as st
    from smm import scenario as sc
    storage = cfg.mode['realtime']['storage']
    if storage.get('mode') == 'sced_anchor' and storage.get('anchor') == 'two_hour_eq':
        st.caption('实时电池SOC与抽蓄上库容：北京时间02:00、04:00…24:00硬一致；中间时段可调。普通水电仍按本场景窗口末规则跟踪。')
        return
    labels = {'hourly_eq': '每小时带罚软目标', 'window_end_pct': '窗口末带罚软区间'}
    current = '固定日前充放电' if storage.get('mode') == 'fixed_da' else labels.get(storage.get('anchor'), '历史策略')
    st.info(f'当前场景仍用“{current}”。旧场景与历史结果不会自动改写。')
    if st.button('本场景改用每2小时SOC/抽蓄库容硬一致', disabled=disabled, key='two_hour_anchor:'+scenario):
        try:
            updated = two_hour_modes(cfg, sc.read_yaml(scenario, 'modes'))
            sc.write_yaml(scenario, 'modes', updated)
        except Exception as exc:
            st.error(f'切换失败：{exc}')
        else:
            st.success('已保存当前场景；下次运行采用新策略，已有结果不变。')
            st.rerun()
