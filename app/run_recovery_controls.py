"""Explicit recovery of absent workers, with no override for unknown ownership."""
from pathlib import Path

import streamlit as st
from smm.run_recovery import ACTIVE, inspect_recovery, recover_run


def render_run_recovery(meta, runs_root):
    if meta.get('status') not in ACTIVE:
        return
    directory = Path(meta['dir'])
    evidence = inspect_recovery(directory)
    if evidence.get('reason') == 'alive':
        return
    with st.expander('运行中断与安全恢复', expanded=True):
        st.warning(evidence['message'])
        st.caption('恢复只把记录标记为中断，不重新求解、不强杀进程、不生成结算；已执行结果、报价、成本和冻结输入全部保留。')
        st.caption('仅支持系统机器身份未被克隆或共享的环境；克隆系统或多个机器共用系统身份时，请停止在此入口恢复并联系管理员。')
        if evidence['eligible']:
            confirmed = st.checkbox('确认保留全部历史文件，并将此运行标记为中断', key='recover_confirm:' + str(directory))
            if st.button('确认进程已不存在并恢复', disabled=not confirmed, key='recover_run:' + str(directory)):
                try:
                    recover_run(directory, runs_root, expected_revision=evidence['revision'])
                except (OSError, ValueError, RuntimeError) as exc:
                    st.error(str(exc))
                else:
                    st.success('已核验中断并解除本条记录占用；历史文件已保留。')
                    st.rerun(scope='app')
        else:
            st.info('若为升级前或其他机器的旧记录，请在原运行机器核对并正常停止后台服务。不要删除运行目录、改写PID或状态来绕过保护。无法取得原机器证据时，请保留原项目，由管理员确认所有原服务已停止后，另建不继承旧runs记录的独立项目副本；原项目与历史全部保留。本应用不能据此认定旧运行已死亡。')
            st.caption('克隆系统、重复或共享系统机器身份不属于可核验环境；不要使用本恢复入口。Windows若无法提供可信启动会话标识，重启后的记录也会保守保留占用。')
