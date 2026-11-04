"""Read-only notice for a stale server or a different local Python/checkout."""
from __future__ import annotations
import os
from pathlib import Path
import sys


def runtime_status(root, *, environ=None, executable=None):
    env=os.environ if environ is None else environ
    root=Path(root).absolute()
    python=Path(executable or sys.executable).absolute()
    expected=env.get('IEEE39_LAUNCH_SOURCE_SHA256')
    issues=[]
    if expected:
        from smm.runtime_identity import source_digest
        try:
            current=source_digest(root)
        except (OSError, ValueError) as exc:
            issues.append('当前源码版本暂不能核对：'+str(exc))
        else:
            if current != expected:
                issues.append('启动后项目代码或默认配置已变化，当前进程可能仍保留旧模块。')
    if env.get('IEEE39_LAUNCH_ROOT') and Path(env['IEEE39_LAUNCH_ROOT']).absolute()!=root:
        issues.append('当前网页目录与启动器记录的项目目录不一致。')
    if env.get('IEEE39_LAUNCH_INTERPRETER') and Path(env['IEEE39_LAUNCH_INTERPRETER']).absolute()!=python:
        issues.append('当前Python与启动器记录的环境不一致。')
    return dict(version_recorded=bool(expected),needs_restart=bool(issues),issues=issues,
                project_root=str(root),python=str(python))


def render_runtime_notice(root):
    import streamlit as st
    status=runtime_status(root)
    st.session_state['runtime_needs_restart']=status['needs_restart']
    if status['needs_restart']:
        st.warning(' '.join(status['issues'])+' 请先在原启动终端按 Ctrl+C 关闭本服务，再从此项目的一键启动入口重新打开。原场景与结果无需删除；不会自动结束其他进程。')
    with st.sidebar.expander('启动环境与版本',expanded=False):
        st.caption('项目：'+status['project_root'])
        st.caption('Python：'+status['python'])
        if not status['version_recorded']:
            st.caption('本次是手工或旧入口启动，未记录启动版本，不能判断是否混用旧模块。pull更新后请关闭旧服务，再用start.bat / start.command / start.sh启动。')
        elif not status['needs_restart']:
            st.caption('启动版本与当前磁盘源码一致；这不是求解或物理可行性检查。')
    return status
