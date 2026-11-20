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
        # A binding-valid but unknown exit remains viewable, without releasing a launch.
        result.update(status=status, state='terminal_saved' if terminal else 'running_or_unknown',
                      block_new_start=not terminal, error=None)
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
