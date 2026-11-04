"""Safe local bootstrap, using only the selected Python's standard library.

Never pulls Git, changes user inputs, removes environments, or installs globally.
Each Python/dependency combination gets a separate, launcher-owned environment.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import platform
import socket
import subprocess
import sys
import time
import traceback
import urllib.error
import urllib.request
import uuid
import webbrowser

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
try:
    from smm.runtime_identity import source_digest  # noqa: E402
except ImportError:
    source_digest = None  # An incomplete pull should get a plain-language error.

SUPPORTED = ((3, 12), (3, 13))
IMPORTS = {
    'pyomo': 'pyomo.environ', 'highspy': 'highspy', 'numpy': 'numpy',
    'pandas': 'pandas', 'scipy': 'scipy', 'PyYAML': 'yaml',
    'ruamel.yaml': 'ruamel.yaml', 'matplotlib': 'matplotlib',
    'streamlit': 'streamlit', 'plotly': 'plotly', 'networkx': 'networkx',
    'psutil': 'psutil',
}


class LaunchFailure(RuntimeError):
    pass


def say(message: str) -> None:
    print(message, flush=True)


def validate_python() -> None:
    if sys.implementation.name != 'cpython' or sys.version_info[:2] not in SUPPORTED:
        raise LaunchFailure(
            f'当前 Python {platform.python_version()} 不在支持范围。请安装 CPython 3.12 或 3.13；'
            'Python 3.11 无法安装当前 scipy/networkx。https://www.python.org/downloads/'
        )


def pinned_requirements(path: Path) -> dict[str, str]:
    """The app manifest intentionally contains only exact package pins."""
    pins = {}
    for line in path.read_text(encoding='utf-8').splitlines():
        line = line.split('#', 1)[0].strip()
        if not line:
            continue
        if line.count('==') != 1 or line.startswith('-'):
            raise LaunchFailure(f'运行依赖文件格式异常：{path.name} 中 {line!r}。请核对代码更新是否完整。')
        name, version = line.split('==')
        if name not in IMPORTS or not version or name in pins:
            raise LaunchFailure(f'运行依赖清单与启动器不一致：{name}。请完整更新代码后重试。')
        pins[name] = version
    if set(pins) != set(IMPORTS):
        raise LaunchFailure('运行依赖清单不完整。请核对 requirements-app.txt 与启动器是否来自同一版本。')
    return pins


def environment_path(root: Path, manifest: Path) -> Path:
    digest = hashlib.sha256(manifest.read_bytes()).hexdigest()[:16]
    version = f'py{sys.version_info.major}{sys.version_info.minor}'
    return root / '.local-app' / 'venvs' / f'{version}-{digest}'


def environment_python(directory: Path, windows: bool | None = None) -> Path:
    if windows is None:
        windows = os.name == 'nt'
    return directory / ('Scripts/python.exe' if windows else 'bin/python')


def child_environment(root: Path = ROOT) -> dict[str, str]:
    # Ignore caller activation/PYTHONPATH so imports cannot come from another checkout.
    env = dict(os.environ)
    for key in ('PYTHONHOME', 'PYTHONPATH', 'VIRTUAL_ENV', 'CONDA_PREFIX'):
        env.pop(key, None)
    allowed_pip = {'PIP_INDEX_URL', 'PIP_EXTRA_INDEX_URL', 'PIP_PROXY', 'PIP_CERT',
                   'PIP_CLIENT_CERT', 'PIP_TIMEOUT', 'PIP_RETRIES', 'PIP_CACHE_DIR'}
    for key in list(env):
        if key.startswith('PIP_') and key not in allowed_pip:
            env.pop(key, None)
    env.update(PYTHONIOENCODING='utf-8', PYTHONUTF8='1', PYTHONNOUSERSITE='1',
               PIP_REQUIRE_VIRTUALENV='true', PIP_DISABLE_PIP_VERSION_CHECK='1',
               PIP_CONFIG_FILE=os.devnull)
    env['MPLCONFIGDIR'] = str(root / '.local-app' / 'cache' / 'matplotlib')
    env['XDG_CACHE_HOME'] = str(root / '.local-app' / 'cache')
    env.setdefault('PIP_CACHE_DIR', str(root / '.local-app' / 'cache' / 'pip'))
    return env


@contextmanager
def launcher_lock(path: Path):
    """OS-owned lock: a crash releases it without stale-PID killing/deletion."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open('a+b')
    try:
        if os.name == 'nt':
            import msvcrt
            if path.stat().st_size == 0:
                handle.write(b'0')
                handle.flush()
            handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise LaunchFailure('这个项目副本已在安装或运行。请使用原启动窗口；不要重复双击。') from exc
        else:
            import fcntl
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise LaunchFailure('这个项目副本已在安装或运行。请使用原启动窗口；不要重复双击。') from exc
        yield
    finally:
        # Closing releases the kernel lock, also when an exception occurs.
        handle.close()


def run_logged(command: list[str], log, root: Path, *, timeout: int | None = None) -> None:
    display = command[:2] + ['<environment and solver check>'] if '-c' in command else command
    log.write('\n$ ' + repr(display) + '\n')
    log.flush()
    try:
        result = subprocess.run(command, cwd=root, env=child_environment(root), stdout=log,
                                stderr=subprocess.STDOUT, timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        raise LaunchFailure('环境检查超时，请查看日志；没有开始市场求解。') from exc
    if result.returncode:
        raise LaunchFailure(f'命令未完成（返回码 {result.returncode}），详见日志。')


def probe_script(pins: dict[str, str], directory: Path) -> str:
    """Validate the actual interpreter, binary imports and the Pyomo/HiGHS bridge."""
    return f'''
import importlib, importlib.metadata as metadata, json, pathlib, sys
assert pathlib.Path(sys.prefix).resolve() == pathlib.Path({str(directory)!r}).resolve(), "Wrong virtual environment"
assert sys.prefix != sys.base_prefix, "Not an isolated virtual environment"
pins = json.loads({json.dumps(pins)!r})
modules = json.loads({json.dumps(IMPORTS)!r})
for name, expected in pins.items():
    actual = metadata.version(name)
    assert actual == expected, f"{{name}}: expected {{expected}}, found {{actual}}"
    importlib.import_module(modules[name])
from pyomo.environ import ConcreteModel, Var, Objective, Constraint, NonNegativeReals, SolverFactory, value
m = ConcreteModel()
m.x = Var(domain=NonNegativeReals)
m.c = Constraint(expr=m.x >= 1)
m.obj = Objective(expr=m.x)
solver = SolverFactory("appsi_highs")
solver.options["time_limit"] = 5
solver.options["threads"] = 1
solver.solve(m)
assert abs(value(m.x) - 1) < 1e-7, "HiGHS smoke test failed"
print("Imports and Pyomo/HiGHS smoke test passed", flush=True)
'''


def validate_environment(python: Path, directory: Path, pins: dict[str, str], log, root: Path) -> None:
    run_logged([str(python), '-m', 'pip', 'check'], log, root, timeout=90)
    run_logged([str(python), '-c', probe_script(pins, directory)], log, root, timeout=90)


def prepare_environment(root: Path, manifest: Path, log, *, check_only: bool = False) -> Path:
    pins = pinned_requirements(manifest)
    directory = environment_path(root, manifest)
    python = environment_python(directory)
    owner = directory / '.ieee39-launcher.json'
    created = directory / '.venv-created.json'
    ready = directory / '.ready.json'
    if check_only:
        if not python.is_file() or not owner.is_file() or not ready.is_file():
            raise LaunchFailure('尚未完成此版本的独立环境安装。请正常启动，或使用 --install-only。')
        validate_environment(python, directory, pins, log, root)
        return python
    if directory.exists() and not owner.is_file():
        raise LaunchFailure(f'环境目录已存在但不是启动器确认管理的目录：{directory}。为保护文件，未覆盖或删除。')
    if not directory.exists():
        directory.mkdir(parents=True)
        owner.write_text(json.dumps({'format': 1, 'root': str(root)}, ensure_ascii=False), encoding='utf-8')
    if not python.is_file() or not created.is_file():
        say('首次准备独立 Python 环境……')
        try:
            run_logged([sys.executable, '-m', 'venv', str(directory)], log, root, timeout=180)
            created.write_text(json.dumps({'python': platform.python_version()}), encoding='utf-8')
        except LaunchFailure as exc:
            raise LaunchFailure('无法创建虚拟环境。请确认 Python 含 venv/ensurepip；'
                                'Debian/Ubuntu 通常需要对应版本的 python3-venv。安装日志保留，可重试。') from exc
    if ready.is_file():
        say('检查依赖与求解器……')
        try:
            validate_environment(python, directory, pins, log, root)
            return python
        except LaunchFailure as exc:
            # Do not upgrade a damaged environment in place or erase evidence.
            raise LaunchFailure('已安装环境检查失败，未启动网页，也未覆盖环境。'
                                '请保留日志，请维护者核对依赖/解释器；可将此环境目录改名备份后重试。') from exc
    say('安装本版本运行依赖，首次需要联网，可能需要几分钟……')
    try:
        run_logged([str(python), '-m', 'pip', 'install', '--prefer-binary', '-r', str(manifest)], log, root)
        validate_environment(python, directory, pins, log, root)
    except LaunchFailure as exc:
        raise LaunchFailure('依赖安装或求解器检查失败，未启动网页。请检查网络、磁盘空间及日志；'
                            '修复后重新双击即可继续，不必删除场景、结果或整个项目。') from exc
    ready.write_text(json.dumps({'requirements': hashlib.sha256(manifest.read_bytes()).hexdigest(),
                                 'python': platform.python_version()}, indent=2), encoding='utf-8')
    return python


def ensure_port_available(port: int) -> None:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind(('127.0.0.1', port))
    except OSError as exc:
        raise LaunchFailure(f'本机端口 {port} 已被占用或不可用。请先关闭原网页服务窗口，'
                            f'或用 --port {port + 1 if port < 65535 else 8501} 指定其他端口；不会结束其他程序。') from exc


def note_source_version(root: Path) -> str:
    digest = source_digest(root)
    marker = root / '.local-app' / 'last-launch.json'
    try:
        previous = json.loads(marker.read_text(encoding='utf-8')).get('source')
    except (OSError, ValueError, AttributeError):
        previous = None
    if previous and previous != digest:
        say('检测到代码或默认配置更新。已有场景、报价和结果保留原样；旧场景请按页面提示明确采用新版设置。')
    marker.write_text(json.dumps({'source': digest, 'root': str(root)}), encoding='utf-8')
    return digest


def start_server(python: Path, root: Path, port: int, log, *, no_browser: bool, initial_digest: str) -> int:
    ensure_port_available(port)
    command = [str(python), '-m', 'streamlit', 'run', str(root / 'app' / '主页.py'),
               '--server.address', '127.0.0.1', '--server.port', str(port),
               '--server.headless', 'true', '--server.fileWatcherType', 'none',
               '--browser.gatherUsageStats', 'false']
    options = {'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == 'nt' else {'start_new_session': True}
    env = child_environment(root)
    env.update(IEEE39_LAUNCH_SOURCE_SHA256=initial_digest,
               IEEE39_LAUNCH_ROOT=str(root), IEEE39_LAUNCH_INTERPRETER=str(python))
    process = subprocess.Popen(command, cwd=root, env=env, stdout=log,
                               stderr=subprocess.STDOUT, **options)
    url = f'http://127.0.0.1:{port}'
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise LaunchFailure(f'网页进程提前退出（返回码 {process.returncode}），请查看日志。')
            try:
                with opener.open(url + '/_stcore/health', timeout=1) as response:
                    healthy = response.status == 200 and response.read().strip() == b'ok'
                if healthy and process.poll() is None:
                    break
            except (OSError, urllib.error.URLError):
                pass
            time.sleep(.2)
        else:
            raise LaunchFailure('网页未在60秒内启动，已停止本次网页进程。请查看日志。')
        say(f'网页已启动：{url}')
        say('请保留此窗口。退出网页服务按 Ctrl+C；如有求解，请先在网页请求取消并等待结束。')
        if not no_browser:
            try:
                if not webbrowser.open(url):
                    say('未能自动打开浏览器，请手动打开上方地址。')
            except Exception:
                say('未能自动打开浏览器，请手动打开上方地址。')
        warned = False
        next_check = time.monotonic() + 10
        while process.poll() is None:
            time.sleep(.3)
            if not warned and time.monotonic() >= next_check:
                next_check = time.monotonic() + 10
                try:
                    changed = source_digest(root) != initial_digest
                except OSError:
                    changed = True
                if changed:
                    say('注意：运行期间项目文件发生变化。请勿继续提交新求解；待当前求解结束后关闭此窗口并重新启动。未自动重启或改写场景。')
                    warned = True
        if process.returncode:
            raise LaunchFailure(f'网页服务退出（返回码 {process.returncode}），请查看日志。')
        return 0
    except KeyboardInterrupt:
        say('\n正在停止本次网页服务。后台求解不会被强杀，历史文件保留。')
        return 0
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='IEEE39 本地首次安装和更新后一键启动（不会 git pull 或覆盖用户数据）。')
    parser.add_argument('--port', type=int, default=8501, help='仅监听本机，默认8501')
    parser.add_argument('--no-browser', action='store_true', help='不自动打开浏览器')
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--install-only', action='store_true', help='准备并检查环境，不启动网页')
    group.add_argument('--check', action='store_true', help='只检查已安装环境，不安装/修复依赖，不启动网页')
    args = parser.parse_args(argv)
    log_path = None
    try:
        if hasattr(sys.stdout, 'reconfigure'):
            sys.stdout.reconfigure(errors='replace')
            sys.stderr.reconfigure(errors='replace')
        validate_python()
        if not 1 <= args.port <= 65535:
            raise LaunchFailure('端口须为1到65535之间的整数。')
        manifest = ROOT / 'requirements-app.txt'
        if source_digest is None or not manifest.is_file() or not (ROOT / 'app' / '主页.py').is_file():
            raise LaunchFailure('项目文件不完整。请核对代码更新，不要删除用户场景来解决此问题。')
        # --check does not create launcher directories or logs.
        if args.check:
            prepare_environment(ROOT, manifest, sys.stdout, check_only=True)
            say('环境、依赖和 HiGHS 检查通过。尚未启动网页或市场求解。')
            return 0
        with launcher_lock(ROOT / '.local-app' / 'launcher.lock'):
            log_dir = ROOT / '.local-app' / 'logs'
            log_dir.mkdir(parents=True, exist_ok=True)
            log_path = log_dir / f'launch-{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:8]}.log'
            say(f'项目：{ROOT}\nPython：{platform.python_version()}\n诊断日志：{log_path}')
            with log_path.open('w', encoding='utf-8') as log:
                try:
                    if not args.install_only:
                        ensure_port_available(args.port)
                    python = prepare_environment(ROOT, manifest, log)
                    digest = note_source_version(ROOT)
                    if args.install_only:
                        say('安装和求解器检查完成。下次双击启动即可。')
                        return 0
                    return start_server(python, ROOT, args.port, log, no_browser=args.no_browser, initial_digest=digest)
                except BaseException:
                    traceback.print_exc(file=log)
                    raise
    except KeyboardInterrupt:
        say('\n安装/检查已中断。下次重新启动可继续，用户文件没有删除。')
        return 130
    except (LaunchFailure, OSError) as exc:
        say(f'\n启动未完成：{exc}')
        if log_path:
            say(f'诊断日志：{log_path}')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
