"""Portable foreground launcher. Does not run a solver or change provenance."""
from __future__ import annotations
import argparse
import errno
import hashlib
import importlib
import os
from pathlib import Path
import socket
import subprocess
import sys

MODULES = ('pyomo', 'highspy', 'numpy', 'pandas', 'scipy', 'yaml', 'ruamel.yaml', 'streamlit', 'plotly', 'networkx')

def python_for(root: Path, current: str, platform: str) -> str:
    relative = ('Scripts', 'python.exe') if platform == 'nt' else ('bin', 'python')
    candidate = root.joinpath('.venv', *relative)
    return str(candidate) if candidate.is_file() else current

def command(root: Path, python: str, port: int, profile: str = "sichuan-v5") -> list[str]:
    if not 1 <= port <= 65535:
        raise ValueError('端口必须在 1–65535 之间')
    if profile not in ('sichuan-v5','guangdong'):
        raise ValueError('未知市场配置')
    entry='四川.py' if profile=='sichuan-v5' else '主页.py'
    cmd=[python, '-m', 'streamlit', 'run', str(root / 'app' / entry),
         '--server.address', '127.0.0.1', '--server.port', str(port),
         '--browser.gatherUsageStats', 'false']
    if profile=='sichuan-v5':
        # No email gate or mixed legacy-page menu on the local Sichuan launcher.
        cmd+=['--server.showEmailPrompt','false','--client.showSidebarNavigation','false',
              '--client.toolbarMode','viewer']
    return cmd

def check(root: Path, profile: str = 'sichuan-v5') -> list[str]:
    errors=[]
    if sys.version_info < (3,12):
        return ['请使用 Python 3.12 或更新版本（推荐 3.12）。当前为 '+sys.version.split()[0]+'。']
    if profile=='sichuan-v5':
        required=('app/四川.py','data/case39/buses.csv','data/case39/branches.csv')
    else:
        required=('app/主页.py',)
    for relative in required:
        if not root.joinpath(relative).is_file():
            errors.append('程序包缺少 '+relative+'；请完整下载并解压对应分支。')
    if errors:
        return errors
    for name in MODULES:
        try:
            importlib.import_module(name)
        except ModuleNotFoundError as exc:
            errors.append('缺少依赖：'+str(exc.name or name))
        except (ImportError,OSError,RuntimeError) as exc:
            errors.append('依赖 '+name+' 已安装但无法加载：'+str(exc))
    if not errors:
        try:
            import pyomo.environ as p
            if not p.SolverFactory('highs').available(exception_flag=False):
                errors.append('HiGHS 无法用于 Pyomo；请在项目虚拟环境重装运行依赖。')
        except (ImportError,OSError,RuntimeError) as exc:
            errors.append('HiGHS 检查失败：'+str(exc))
    return list(dict.fromkeys(errors))

def _bind_loopback(port: int) -> None:
    # Match the application's IPv4 loopback bind. Allow immediate POSIX
    # restart after a closed connection; never enable Windows shared binding.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        if os.name != 'nt':
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(('127.0.0.1', port))


def _occupied(exc: OSError) -> bool:
    return exc.errno == errno.EADDRINUSE or getattr(exc, 'winerror', None) == 10048


def _port_message(port: int, exc: OSError) -> str:
    if _occupied(exc):
        return f'本机端口 {port} 已被占用，未启动此程序。'
    return f'无法绑定本机端口 {port}，未启动此程序：{exc}'


def check_port(port: int) -> str | None:
    """Return the preflight diagnostic for callers testing one chosen port."""
    try:
        _bind_loopback(port)
    except OSError as exc:
        return _port_message(port, exc)
    return None


def choose_sichuan_port(requested: int | None) -> int:
    """Try IPv4 loopback binds only. Never contact or terminate the occupant."""
    if requested is not None and not 1 <= requested <= 65535:
        raise ValueError('端口必须在 1–65535 之间')
    candidates = (requested,) if requested is not None else range(8501, 8511)
    for port in candidates:
        try:
            _bind_loopback(port)
        except OSError as exc:
            if requested is None and _occupied(exc):
                continue
            message = _port_message(port, exc)
            if _occupied(exc):
                message += ' 请用 --port 指定其他空闲端口，或省略 --port 让程序自动选择。'
            raise ValueError(message) from exc
        return port
    raise ValueError('本机默认端口 8501–8510 均已占用，请用 --port 指定其他空闲端口。')


def view_source_id(root: Path) -> str:
    source = (root / 'app' / 'sichuan_v5_dadu_view.py').read_bytes()
    return hashlib.sha256(source.replace(b'\r\n', b'\n')).hexdigest()[:12]


def main(argv=None):
    parser=argparse.ArgumentParser(description='本机启动市场模拟界面；此步骤不运行出清。')
    parser.add_argument('--check', action='store_true', help='仅检查环境，不启动网页')
    parser.add_argument('--profile',choices=('sichuan-v5','guangdong'),default='sichuan-v5',help='默认四川征求意见稿研究；显式选 guangdong 才启动广东历史入口')
    parser.add_argument('--port', type=int, help='指定本机端口；四川模式默认从 8501–8510 选择首个空闲端口')
    args=parser.parse_args(argv)
    root=Path(__file__).resolve().parents[1]
    interpreter=python_for(root,sys.executable,os.name)
    # Re-enter once using the project's environment, rather than a system streamlit.
    if Path(interpreter).absolute() != Path(sys.executable).absolute():
        return subprocess.call([interpreter, str(Path(__file__).resolve()), *(argv if argv is not None else sys.argv[1:])])
    errors=check(root,args.profile)
    if errors:
        print('\n'.join(errors),file=sys.stderr)
        print('首次安装请看本目录 SICHUAN_V5.md。安装命令：项目虚拟环境的 python -m pip install -r requirements-runtime.txt',file=sys.stderr)
        return 2
    env={**os.environ,'PYTHONUTF8':'1'}
    if args.profile=='sichuan-v5':
        try:
            source_id = view_source_id(root)
        except OSError as exc:
            print(f'无法读取结果视图源文件：{exc}', file=sys.stderr)
            return 2
        env['SICHUAN_UI_SOURCE_ID'] = source_id
    if args.check:
        print('启动检查通过（程序、依赖与 HiGHS 可用）；尚未运行市场求解。')
        if args.profile=='sichuan-v5':
            print(f'入口：{root / "app" / "四川.py"}\n结果视图版本 · {source_id}')
        return 0
    try:
        port = (choose_sichuan_port(args.port) if args.profile=='sichuan-v5'
                else (8501 if args.port is None else args.port))
        cmd = command(root, interpreter, port, args.profile)
    except ValueError as exc:
        print(str(exc),file=sys.stderr)
        return 2
    if args.profile=='sichuan-v5':
        if args.port is None and port!=8501:
            print(f'默认端口 8501 已被占用，本次选择 {port}。')
        print(f'入口：{cmd[4]}\n结果视图版本 · {source_id}')
        # Only Streamlit announces the actual URL, after winning the bind race.
        print(f'本次端口 {port}；启动成功后请打开终端显示的本机地址。停止网页按 Ctrl+C。',flush=True)
    else:
        print('本机访问：http://127.0.0.1:'+str(port)+'；停止网页按 Ctrl+C。')
    try: return subprocess.call(cmd,cwd=root,env=env)
    except KeyboardInterrupt: return 130

if __name__=='__main__': raise SystemExit(main())
