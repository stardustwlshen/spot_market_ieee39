"""Portable foreground launcher. Does not run a solver or change provenance."""
from __future__ import annotations
import argparse
import importlib
import os
from pathlib import Path
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

def main(argv=None):
    parser=argparse.ArgumentParser(description='本机启动市场模拟界面；此步骤不运行出清。')
    parser.add_argument('--check', action='store_true', help='仅检查环境，不启动网页')
    parser.add_argument('--profile',choices=('sichuan-v5','guangdong'),default='sichuan-v5',help='默认四川征求意见稿研究；显式选 guangdong 才启动广东历史入口')
    parser.add_argument('--port', type=int, default=8501)
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
    if args.check:
        print('启动检查通过（程序、依赖与 HiGHS 可用）；尚未运行市场求解。')
        return 0
    try: cmd=command(root, interpreter, args.port,args.profile)
    except ValueError as exc:
        print(str(exc),file=sys.stderr);return 2
    print('本机访问：http://127.0.0.1:'+str(args.port)+'；停止网页按 Ctrl+C。')
    env={**os.environ,'PYTHONUTF8':'1'}
    try: return subprocess.call(cmd,cwd=root,env=env)
    except KeyboardInterrupt: return 130

if __name__=='__main__': raise SystemExit(main())
