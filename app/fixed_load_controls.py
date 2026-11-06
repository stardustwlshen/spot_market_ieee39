"""Explicit restoration of external supply; never edits historical snapshots."""
from copy import deepcopy


def fixed_load_base(base):
    """Historical helper retained for compatibility, no longer offered in the UI."""
    result = deepcopy(base)
    result['tie_line_mode'] = 'fixed_load'
    result['tie_load_allocation'] = 'base_load_share'
    return result


def render_fixed_load_policy(cfg, scenario, disabled=False):
    import streamlit as st
    from smm.fixed_load import tie_mode
    from smm.tie_restore import prepare_supply_restore, apply_supply_restore
    if tie_mode(cfg) == 'supply':
        st.caption('区外来电：固定计划供给。')
        return
    st.warning('此场景仍保留已取消的“来电转负荷”设置。请恢复区外来电后再运行；已有历史结果仍按原冻结边界显示。')
    st.caption('恢复会先备份本场景原配置与受影响曲线，再取消TIE需求覆盖并恢复原固定来电计划；基础负荷/风光、报价、装机与线路不变。旧典型组合的负荷基准会归档，恢复后的当前曲线成为新组合起点。')
    if st.button('备份并恢复本场景区外来电（下次运行）', disabled=disabled, key='restore_tie_supply:'+scenario):
        try:
            result = apply_supply_restore(prepare_supply_restore(scenario))
        except (OSError, ValueError, TypeError, KeyError, RuntimeError) as exc:
            from input_workflow import business_error
            business_error('未恢复区外来电，请检查场景输入', exc)
        else:
            st.session_state['typical_created_notice'] = '已备份并恢复当前场景区外来电。下次运行使用供电注入；报价与历史结果保留。'
            st.rerun()
