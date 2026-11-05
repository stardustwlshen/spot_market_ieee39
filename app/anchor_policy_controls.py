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
        st.caption('当前储能/抽蓄采用两小时锚点：北京时间02:00、04:00…24:00状态以日前计划为硬目标，中间时段可调。'
                   '明确不可行时才按既定规则尝试软化并记录；水电保持本场景单独配置的处理方式。')
        return
    labels = {'hourly_eq': '每小时带罚软目标', 'window_end_pct': '窗口末带罚软区间'}
    mode = storage.get('mode', 'value')
    if mode == 'value':
        current = '价值法'
        st.info('当前储能/抽蓄采用价值法：使用所选参考的状态价值与中间偏差带，物理约束保持。')
        policy = cfg.mode['realtime'].get('value_method', {}).get('terminal_policy') or {}
        terminal_mode = policy.get('mode', 'hard')
        if terminal_mode == 'soft':
            rate = policy.get('penalty_yuan_per_discharge_mwh')
            rate_label = f'{rate:g}' if isinstance(rate, (int, float)) and not isinstance(rate, bool) else '未记录'
            st.caption(f'当前实时日末为计划软目标，罚价 {rate_label} 元/可放电MWh（可配置研究假设，不是市场规则或结算付款）。'
                       '储能/抽蓄对日前日末目标的双侧偏差计成本；水电若采用价值法，仅对低于原日末下限的不足计成本。'
                       '从首次求解起即允许带成本偏差，不是硬目标失败后的应急兜底，不保证偏差在某百分比以内。'
                       '中间参考带仍按配置使用，取消严格回归日末的可达包络，日末不再额外保留±2%硬带。'
                       '日前、日内终态政策不变；日末延续价值仍为0。')
        elif terminal_mode == 'hard':
            st.caption('当前实时日末采用原硬目标政策；历史场景缺少新日末政策配置时也保持hard，浏览不会自动改成软目标。'
                       '日末延续价值为0与硬目标要求并不冲突。')
        else:
            st.warning(f'实时日末政策“{terminal_mode}”未识别；请核对本场景配置，不推断软/硬。')
    else:
        current = '固定日前充放电' if mode == 'fixed_da' else labels.get(storage.get('anchor'), '已保存策略')
        st.info(f'当前储能/抽蓄采用“{current}”。浏览本页不会改变场景或历史结果。')
    with st.expander('可选研究策略：两小时锚点（改变本场景）', expanded=False):
        st.caption(f'这是可选模型切换，不是升级要求。采用后将本场景储能/抽蓄从“{current}”改为每两小时跟随日前状态的锚点策略；'
                   '明确不可行时才按既定规则尝试软化并记录。水电配置和已保存结果不变，不能据此保证计算更优。')
        if st.button('将本场景切换到两小时锚点策略', disabled=disabled, key='two_hour_anchor:'+scenario):
            try:
                updated = two_hour_modes(cfg, sc.read_yaml(scenario, 'modes'))
                sc.write_yaml(scenario, 'modes', updated)
            except Exception as exc:
                st.error(f'切换失败：{exc}')
            else:
                st.success('已明确切换当前场景；下次运行采用两小时锚点，已有结果不变。')
                st.rerun()
