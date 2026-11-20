"""Bounded saved-task discovery. Read-only tracking, never execution permission.

No plan/start/resume calls. A fresh browser session does not imply a fresh run.
Unknown/incomplete evidence blocks new starts; PID files never prove liveness.
"""
from pathlib import Path
import json,os,hashlib

def terminal_status(status):
    """Only explicit, known terminal receipts may unblock a new attempt."""
    from gd_full_day_ui import day_exit_kind
    return day_exit_kind(status) != 'unknown'

MAX_ENTRIES = 200
MAX_TASKS = 32
MAX_JSON_BYTES = 8 * 1024 * 1024

def _read(path):
    if path.is_symlink() or not path.is_file():
        raise ValueError('缺少普通证据文件：' + path.name)
    with path.open('rb') as f:
        data = f.read(MAX_JSON_BYTES + 1)
    if len(data) > MAX_JSON_BYTES:
        raise ValueError('证据文件过大：' + path.name)
    obj = json.loads(data)
    if not isinstance(obj, dict):
        raise ValueError('证据格式错误：' + path.name)
    return obj

def _children(path):
    if path.is_symlink():
        raise ValueError('不跟随任务目录链接：' + str(path))
    with os.scandir(path) as entries:
        result = []
        for entry in entries:
            if len(result) >= MAX_ENTRIES:
                raise ValueError('任务目录过多，无法完整核对')
            result.append(Path(entry.path))
    return sorted(result)

def _status(output, control):
    if control is None:
        import gd_live_control as control
    return control.status(str(output))

def _read_pinned(path, expected):
    # Read with the scanner's size bound and never follow an evidence link.
    if path.is_symlink() or path.resolve() != path or not path.is_file():
        raise ValueError('证据路径不匹配：' + path.name)
    with path.open('rb') as f:
        data = f.read(MAX_JSON_BYTES + 1)
    if len(data) > MAX_JSON_BYTES or hashlib.sha256(data).hexdigest() != expected:
        raise ValueError('证据校验不匹配：' + path.name)
    result = json.loads(data)
    if not isinstance(result, dict):
        raise ValueError('证据格式错误：' + path.name)
    return result

def _completed_first_hour(output, plan, status, end):
    """Only the native four-window receipt family; never a whole-day result."""
    if not isinstance(end, dict) or end.get('status') != 'complete_research':
        return False
    if plan.get('windows') != 4 or type(plan['windows']) is not int:
        return False
    branch = str(output / 'pipeline/dispatch')
    pins = plan.get('source_pins')
    clock = plan.get('pricing_clock_path')
    if (plan.get('logical_source_branch') != branch
        or plan.get('execution_attempt_id') != output.name
        or plan.get('model_scope') != 'active_power_dc'
        or not isinstance(clock, str) or not Path(clock).is_absolute()
        or not isinstance(pins, dict)
        or not all(pins.get(x) for x in (clock, str(Path(plan['run']) / 'SHORT_CONTEXT.json'), str(Path(plan['run']) / 'seed/case.json')))):
        raise ValueError('首小时计划的计算来源不完整')
    hours = status.get('hours')
    quarters = status.get('quarters')
    if (type(status.get('completed_windows')) is not int or status['completed_windows'] != 4
        or not isinstance(quarters, list) or len(quarters) != 4
        or not all(isinstance(q, dict) and type(q.get('quarter')) is int and q['quarter'] == w
                   for w, q in enumerate(quarters))
        or not isinstance(hours, list) or len(hours) != 1):
        raise ValueError('首小时调度、定价或小时记录不完整')
    pipeline = output / 'pipeline'
    receipt_path = pipeline / 'COMPLETE.json'
    if receipt_path.resolve() != receipt_path:
        raise ValueError('首小时结束凭证路径不匹配')
    receipt = _read(receipt_path)
    hour = pipeline / 'price/HOUR0.json'
    original = _read_pinned(hour, _hour_digest(status, hour))
    summary = end.get('hour')
    run_id = original.get('run_id')
    if (receipt.get('status') != 'complete_research'
        or type(receipt.get('windows')) is not int or receipt['windows'] != 4
        or type(receipt.get('native_calls')) is not int or receipt['native_calls'] != 12
        or type(receipt.get('AC_calls')) is not int or receipt['AC_calls'] != 0
        or receipt.get('formal') is not False or receipt.get('safety_release') is not False
        or end.get('model_scope') != 'active_power_dc' or end.get('safety_release') is not False
        or end.get('publication_allowed') is not False
        or not isinstance(run_id, str) or not run_id or receipt.get('branch_id') != run_id
        or original.get('branch') != branch or original.get('schema') != 'gd.live_host.PRICE.hour.v3'
        or original.get('executed_cells') != 4 or original.get('quarter_prices') != 4
        or original.get('safety_release') is not False or original.get('formal_bill') is not None
        or hours[0] != original or status.get('hour') != original
        or not isinstance(summary, dict) or receipt.get('hour') != summary
        or type(summary.get('executed_cells')) is not int or summary['executed_cells'] != 4
        or type(summary.get('native_calls')) is not int or summary['native_calls'] != 0):
        raise ValueError('首小时结束凭证或小时来源不匹配')
    hour_pins = original.get('source_pins')
    if not isinstance(hour_pins, dict):
        raise ValueError('首小时缺少定价来源校验')
    execution = pipeline / 'price/EXECUTION_H0.json'
    executed = _read_pinned(execution, hour_pins.get(str(execution)))
    cells = executed.get('cells')
    if (executed.get('schema') != 'gd.live_host.execution_view.v3' or executed.get('run_id') != run_id
        or executed.get('run') != branch or executed.get('base_run') != plan['run']
        or executed.get('selected_DA') != plan['da'] or type(executed.get('hour')) is not int
        or executed['hour'] != 0 or type(executed.get('executed_end')) is not int
        or executed['executed_end'] != 4 or not isinstance(cells, list)
        or any(type(cell) is not int for cell in cells) or cells != [0, 1, 2, 3]):
        raise ValueError('首小时执行记录与运行来源不匹配')
    for w, quarter in enumerate(quarters):
        accepted = pipeline / f'price/q{w}/ACCEPTED.json'
        raw = _read_pinned(accepted, hour_pins.get(str(accepted)))
        ctx = raw.get('context', {})
        prices = raw.get('raw_prices')
        if (ctx.get('short_run') != branch or ctx.get('run_id') != run_id
            or ctx.get('selected_DA') != plan['da'] or type(ctx.get('day_quarter')) is not int
            or ctx['day_quarter'] != w or raw.get('schema') != 'gd.independent_PRICE.accepted.v2'
            or raw.get('KKT', {}).get('passed') is not True
            or not isinstance(prices, dict) or len(prices) != 39
            or quarter.get('raw_prices') != prices):
            raise ValueError('首小时定价记录与运行来源不匹配')
    return True

def _hour_digest(status, hour):
    exports = status.get('hour_exports')
    if (not isinstance(exports, list) or len(exports) != 1 or not isinstance(exports[0], dict)
        or exports[0].get('path') != str(hour) or not isinstance(exports[0].get('sha256'), str)):
        raise ValueError('首小时结果导出路径不匹配')
    return exports[0]['sha256']

def read_tracking(record, control=None):
    """Recheck a discovered RT task; never restore a launchable ready object."""
    output = Path(record['output'])
    result = dict(record)
    try:
        if output.is_symlink() or output.resolve() != output:
            raise ValueError('任务路径发生变化')
        plan = _read(output / 'PLAN.json')
        windows = plan.get('windows', 4)
        if type(windows) is not int or not 1 <= windows <= 96:
            raise ValueError('计划实时段数无效，无法核对')
        if plan.get('output') != str(output):
            raise ValueError('计划输出目录不匹配')
        run_root = output.parent.parent
        if output.parent.name != 'live_market_sessions' or plan.get('run') != str(run_root / 'rt') or plan.get('da') != str(run_root / 'da'):
            raise ValueError('计划来源目录不匹配')
        status = _status(output, control)
        if not isinstance(status, dict):
            raise ValueError('状态格式错误')
        end = status.get('exit')
        # EXIT is a saved terminal receipt, not proof of no remaining process.
        # Only known terminal statuses are displayable as terminal here.
        terminal = terminal_status(status)
        if end is not None and _read(output / 'EXIT.json') != end:
            raise ValueError('结束记录与状态读取不一致')
        if terminal and end.get('status') == 'complete_research' and windows != 96:
            raise ValueError('全天结果与计划段数不一致')
        short = not terminal and _completed_first_hour(output, plan, status, end)
        # Keep full-day status separate from the terminal short-run evidence.
        state = 'first_hour_saved' if short else 'terminal_saved' if terminal else 'running_or_unknown'
        result.update(status=status, state=state, block_new_start=not (terminal or short), error=None)
    except Exception as exc:
        result.update(state='unknown', status=None, block_new_start=True, error=str(exc))
    result['read_only'] = True
    result['execution_authorized'] = False
    return result

def _day(root):
    result = dict(kind='DA', output=str(root), name=root.name, read_only=True,
                  execution_authorized=False, state='unknown', block_new_start=True)
    try:
        progress = _read(root / 'PROGRESS.json')
        result['progress'] = progress
        if progress.get('status') == 'failed':
            stage = progress.get('stage')
            if type(stage) is not int or not 0 <= stage <= 3:
                raise ValueError('失败步骤不明')
            receipt=progress.get('prelaunch_failure')
            if receipt is not None:
                keys={'schema','stage','reason','output','ui_input_sha256','launched_workers'}
                if not isinstance(receipt,dict) or set(receipt)!=keys:
                    raise ValueError('启动前终止凭证格式错误')
                if (stage != 0 or type(receipt['stage']) is not int or receipt['stage'] != 0
                    or type(receipt['launched_workers']) is not int or receipt['launched_workers'] != 0
                    or receipt['schema'] != 'gd.da.no_launch.v1'
                    or receipt['reason'] != 'source_identity_before_first_launch'
                    or receipt['output'] != str(root)
                    or any(progress.get(k) for k in ('timed_out','native_completion_unknown'))):
                    raise ValueError('启动前终止凭证不一致')
                if {p.name for p in root.iterdir()} != {'UI_INPUT.json','PROGRESS.json'}:
                    raise ValueError('启动前终止目录存在其他执行证据')
                ui=_read(root/'UI_INPUT.json')
                digest=hashlib.sha256(json.dumps(ui,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
                if digest != receipt['ui_input_sha256']:
                    raise ValueError('启动前输入凭证不匹配')
                result.update(state='failed_saved',block_new_start=False,error=progress.get('error'),verification_scope='verified stage-zero no-launch failure; explicit new attempt only')
                return result
            step = _read(root / ('STEP_' + str(stage) + '.json'))
            if type(step.get('returncode')) is int and step['returncode'] != 0 and not any(z.get(k) for z in (progress, step) for k in ('timed_out', 'native_completion_unknown')):
                result.update(state='failed_saved', block_new_start=False, error=progress.get('error'), verification_scope='recorded wrapper nonzero exit; no automatic retry')
                return result
            raise ValueError('失败步骤退出未确认，不能自动重算')
        if progress.get('status') != 'ready' or progress.get('stage') != 4:
            raise ValueError('日前尚未确认准备完成；保留现场，不自动重算')
        for i in range(4):
            step = _read(root / ('STEP_' + str(i) + '.json'))
            if type(step.get('returncode')) is not int or step['returncode'] != 0 or step.get('native_completion_unknown') or step.get('timed_out'):
                raise ValueError('日前步骤未确认完成')
        for folder in ('da', 'rt', 'rt/seed'):
            if (root / folder).is_symlink():
                raise ValueError('不跟随来源目录链接')
        _read(root / 'UI_INPUT.json')
        _read(root / 'da/MANIFEST.json')
        context = _read(root / 'rt/SHORT_CONTEXT.json')
        if context.get('selected_DA') != str(root / 'da'):
            raise ValueError('日前与实时来源绑定不一致')
        _read(root / 'rt/seed/case.json')
        result.update(state='ready_saved', block_new_start=False,
                      verification_scope='saved readiness receipts only; not new source validation or permission to execute', error=None)
    except Exception as exc:
        result['error'] = str(exc)
    return result

def discover(results_root, control=None):
    """Scan current root/run/live_market_sessions/task only. Never auto-select."""
    root = Path(results_root).expanduser()
    result = dict(root=str(root), days=[], tasks=[], warnings=[], block_new_start=False,
                  read_only=True, execution_authorized=False)
    def warning(message):
        result['warnings'].append(message)
        result['block_new_start'] = True
    try:
        if root.is_symlink():
            raise ValueError('结果根目录是链接，请指定实际目录')
        root = root.resolve()
        result['root'] = str(root)
        if not root.exists():
            return result
        for day in _children(root):
            if day.is_symlink():
                warning('未核对链接目录：' + day.name)
                continue
            if not day.is_dir():
                continue
            # Input snapshots are not tasks. Any actual attempt with saved
            # evidence is retained, including failed DA before any RT PLAN.
            if any((day / n).exists() for n in ('UI_INPUT.json', 'PROGRESS.json', 'STEP_0.json', 'da', 'rt')):
                row = _day(day)
                result['days'].append(row)
                result['block_new_start'] |= row['block_new_start']
            sessions = day / 'live_market_sessions'
            if not sessions.exists() and not sessions.is_symlink():
                continue
            try:
                for task in _children(sessions):
                    if len(result['tasks']) >= MAX_TASKS:
                        raise ValueError('实时任务超过 32 个；请选更小的结果根目录或保留现场后分开归档')
                    if not task.is_dir() and not task.is_symlink():
                        warning('实时任务目录包含未知文件：' + str(task))
                        continue
                    row = read_tracking(dict(kind='RT', output=str(task), name=task.name), control)
                    result['tasks'].append(row)
                    result['block_new_start'] |= row['block_new_start']
            except Exception as exc:
                warning(str(exc))
    except Exception as exc:
        warning(str(exc))
    return result
