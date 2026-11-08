"""Show the stored duration and offer the missing-key repair. Never rewrites other values."""
import streamlit as st

from input_workflow import business_error


def render_duration_repair(name):
    from smm.reserve_duration_repair import inspect_scene_duration, repair_missing_reserve_duration
    from smm.scenario import scenario_path
    try:
        info = inspect_scene_duration(scenario_path(name))
    except (OSError, ValueError, TypeError) as exc:
        st.caption('备用持续时间未能单独核对：' + str(exc))
        return
    if info['journal']:
        st.warning(info['message'])
        return
    if info['ok'] or not info['message']:
        return
    if 'reserve.T_dur_h' not in info['message']:
        return
    st.caption(
        f"备用持续时间：状态 {info['state']}，当前值 {info['value']!r}，类型 {info['value_type']}，"
        f"文件 {info['path']}。"
    )
    if not info['repair_allowed']:
        st.caption('已有值不会改成 0.25。请按技术详情自行核对这一项，其他配置保持不动。')
        return
    st.info('键完全缺失。可以只补仓库模板已有的 15 分钟研究假设 0.25 小时。会先备份 modes.yaml，不改其他项，也不覆盖整份配置。')
    if st.button('备份并补上缺失的备用持续时间 0.25 小时', key='repair_missing_tdur:' + name):
        try:
            backup = repair_missing_reserve_duration(scenario_path(name))
        except (OSError, ValueError, TypeError, RuntimeError) as exc:
            business_error('未补写备用持续时间，原文件保留', exc)
        else:
            st.success('已只补上缺失的 T_dur_h=0.25。备份：' + str(backup))
            st.rerun()
