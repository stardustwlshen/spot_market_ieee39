"""Evidence-only progress: solver boundaries and executed counts, never ETA weights."""
from datetime import datetime, timezone
import json
from pathlib import Path

ACTIVE = {'running', 'starting', 'cancelling'}
PHASES = {
    'preparing': '启动与输入准备', 'da_start': '日前 DA：准备模型', 'DA': '日前 DA：准备模型',
    'da_done': '日前 DA 已完成，等待后续阶段事件', 'rolling': '准备日内 / 实时滚动',
    'id_round': '日内 ID 本轮已结束，等待后续阶段事件',
    'rt_window': '实时 RT 本时段已执行，等待后续阶段事件',
    'daily_energy_audit': '核验水电日电量约束', 'result_tables': '整理执行结果',
    'settlement': '计算偏差结算', 'execution_checks': '核验执行与结算结果',
    'full_complete': '全流程计算已返回，等待写出结果', 'writing_outputs': '写出结果',
}
DA_ACTIVITIES = {'build': '构建模型', 'scuc': 'SCUC 机组组合求解',
                 'fix_integers': '固定整数与准备定价', 'sced': '固定整数 SCED 定价求解',
                 'auxiliary_pricing': '辅助定价求解（数量不执行）'}
DA_STAGES = {'scuc_preplan': 'SCUC 预计划', 'agc_auxiliary': '辅助容量分配',
             'energy_sced': '固定整数 SCED', 'auxiliary_pricing': '辅助定价'}


def _count(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def describe_progress(meta, live, activity=None, log=''):
    """Pure display model. Terminal process metadata always overrides older events."""
    activity = activity or {}
    # Either telemetry file may have been temporarily write-blocked. Do not let
    # an older small record override newer completed-stage evidence.
    try:
        if activity.get('updated_at') and live.get('updated_at') and datetime.fromisoformat(live['updated_at']) > datetime.fromisoformat(activity['updated_at']):
            activity = {}
    except (ValueError, TypeError):
        pass
    phase = activity.get('phase') or live.get('phase')
    state = meta.get('status', 'unknown')
    stage = PHASES.get(phase, '等待进度事件')
    if phase == 'da_activity':
        stage = '日前 DA：' + DA_ACTIVITIES.get(activity.get('activity'), '等待已知子阶段事件')
    elif phase == 'da_stage':
        last = activity.get('stage') or live.get('stage')
        if not last and isinstance(live.get('da_stages'), dict):
            last = next(reversed(live['da_stages']), None)
        stage = '日前 DA：' + DA_STAGES.get(last, '已记录子阶段') + '已完成，等待下一阶段事件'
    elif phase in ('rt_start', 'id_start'):
        period = _count(activity.get('period'))
        total = _count(activity.get('total'))
        kind = '实时 RT' if phase == 'rt_start' else '日内 ID'
        stage = f'{kind}：第 {period + 1} / {total} 时段窗口求解' if period is not None and total and period < total else f'{kind}：窗口求解（时段未记录）'
    # Legacy records can establish the last logged boundary, not a guessed next step.
    elif not activity and phase in (None, 'preparing', 'da_start'):
        da_lines = [line for line in log.splitlines() if line.startswith('[DA]')]
        if da_lines:
            last = da_lines[-1]
            if '开始 SCUC' in last:
                stage = '日前 DA：SCUC 机组组合求解（最后日志事件）'
            elif 'SCED(LP)' in last:
                stage = '日前 DA：固定整数 SCED 已完成，等待下一阶段事件'
            elif 'SCUC ' in last:
                stage = '日前 DA：SCUC 已返回，等待下一阶段事件'
    if state == 'done':
        scope = meta.get('completed_scope') or live.get('completed_scope')
        stage = ('仅日前计划完成（无实时与结算）' if scope == 'da_only' or meta.get('mode') == 'da' else
                 '调试前缀完成（非完整日结算）' if scope == 'debug_prefix' else '所选范围计算完成，请查看结果核验')
    elif state in ('failed', 'stopped', 'cancelled', 'timeout', 'timed_out'):
        stage = {'failed': '运行失败，未完成结算', 'stopped': '已取消，执行前缀保留，未完成结算',
                 'cancelled': '已取消，未完成结算', 'timeout': '运行已超时，完成范围未确认',
                 'timed_out': '运行已超时，完成范围未确认'}[state]
    elif state == 'starting' and stage == '等待进度事件':
        stage = '启动中，等待后台首个进度事件'
    elif state == 'cancelling':
        stage = '已请求取消，等待安全边界；最后阶段：' + stage
    completed = _count(activity.get('executed_periods', live.get('executed_periods')))
    total = _count(activity.get('total_periods', live.get('total_periods')))
    count = (completed, total) if completed is not None and total and completed <= total else None
    return dict(stage=stage, status=state, phase=phase, count=count,
                updated_at=activity.get('updated_at') or live.get('updated_at'))


def read_progress(directory, *, include_live=True):
    """Read only; corrupt/missing progress never prevents seeing process status."""
    directory = Path(directory)
    values, warnings = {}, []
    for name in ('progress', 'live'):
        if name == 'live' and not include_live and values.get('progress'):
            try:
                fresh = not (directory/'live.json').exists() or (directory/'progress.json').stat().st_mtime_ns >= (directory/'live.json').stat().st_mtime_ns
            except OSError:
                fresh = False
            if fresh:
                values[name] = {}  # Small run-step card does not need result tables.
                continue
        path = directory / (name + '.json')
        try:
            value = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
            if not isinstance(value, dict):
                raise ValueError('根节点不是对象')
            values[name] = value
        except (OSError, ValueError) as exc:
            values[name] = {}
            warnings.append(f'{name}.json 暂不能读取：{exc}')
    log_path = directory/'run.log'
    try:
        # Bounded tail: no growing log scan on each 2-second refresh.
        with log_path.open('rb') as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 65536))
            log = f.read().decode('utf-8', errors='replace')
    except FileNotFoundError:
        log = ''
    except OSError as exc:
        log = ''
        warnings.append(f'run.log 暂不能读取：{exc}')
    modified = []
    for name in ('progress.json', 'live.json', 'run.log'):
        try:
            modified.append((directory/name).stat().st_mtime)
        except OSError:
            pass
    return dict(live=values['live'], activity=values['progress'], log=log, warnings=warnings,
                modified=max(modified) if modified else None)


def render_progress_card(meta, evidence=None):
    import streamlit as st
    from run_record_actions import elapsed_for_display
    from common import STATUS_CN
    evidence = evidence or read_progress(meta['dir'])
    model = describe_progress(meta, evidence['live'], evidence['activity'], evidence['log'])
    st.info('当前阶段：' + model['stage'])
    left, middle, right = st.columns(3)
    left.metric('运行状态', {**STATUS_CN, 'timeout':'已超时', 'timed_out':'已超时', 'unknown':'状态未知', 'cancelled':'已取消'}.get(model['status'], model['status']))
    elapsed, estimated = elapsed_for_display(meta)
    middle.metric('已用时（墙钟秒）' if estimated else '已保存耗时（秒）', f'{elapsed:.0f}' if elapsed is not None else '未知')
    last = model['updated_at']
    try:
        stamp = datetime.fromisoformat(last.replace('Z', '+00:00')) if last else (
            datetime.fromtimestamp(evidence['modified'], tz=timezone.utc) if evidence['modified'] is not None else None)
        right.metric('最后事件时间（UTC）' if last else '最后文件更新（UTC）', stamp.astimezone(timezone.utc).strftime('%H:%M:%S') if stamp and stamp.tzinfo else '未记录')
        if stamp and stamp.tzinfo:
            age = max(0., (datetime.now(timezone.utc) - stamp).total_seconds())
            st.caption(f'距最后记录 {age:.0f} 秒。页面每 2 秒读取进度；没有新事件不等于求解停止。')
    except (ValueError, TypeError, OverflowError):
        right.metric('最后事件时间', '无法解析')
    if model['count'] and (model['count'][0] > 0 or model['phase'] in ('rt_start', 'rt_window', 'id_start')):
        completed, total = model['count']
        st.progress(completed / total, text=f'实时已执行 {completed} / {total} 时段（仅执行计数，不代表结算完成）')
    elif model['status'] in ACTIVE:
        st.caption('求解尚无可用完成百分比。阶段按真实事件显示，不用耗时估算整体进度。')
    if model['status'] in ACTIVE:
        st.caption('单次求解可能较久；达到求解器时限但保有可行解，不等于整体运行失败。')
    telemetry_lines = [line for line in evidence['log'].splitlines() if line.startswith(('[progress warning]', '[progress recovered]'))]
    if telemetry_lines and telemetry_lines[-1].startswith('[progress warning]'):
        st.warning('进度文件写入暂时受阻，阶段或明细可能滞后；这不等于求解失败。请以运行终态和已保存结果核验为准。')
    for warning in evidence['warnings']:
        st.warning(warning + '；保留原文件，当前阶段可能不完整。')
    return evidence


def render_active_progress(directory):
    """Small live card on the run step; do not force navigation on timer refresh."""
    import streamlit as st
    from common import sc, run_status
    @st.fragment(run_every=2)
    def monitor():
        record = next((m for m in sc.list_runs() if m['dir'] == directory), None)
        if record is None:
            st.info('当前运行记录暂不可见，请查看进度与结果。')
            return
        record['status'] = run_status(record)
        render_progress_card(record, read_progress(record['dir'], include_live=False))
        if record['status'] not in ACTIVE:
            st.rerun(scope='app')
    monitor()
