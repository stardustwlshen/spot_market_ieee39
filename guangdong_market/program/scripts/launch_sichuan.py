"""Portable foreground launcher. Does not run a solver or change provenance."""
from __future__ import annotations
import argparse
import importlib.util
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

def check(root: Path) -> list[str]:
    errors=[]
    if sys.version_info < (3, 12):
        errors.append('请使用 Python 3.12 或更新版本（推荐 3.12）。')
    for entry in ('四川.py','主页.py'):
        if not (root / 'app' / entry).is_file():errors.append('程序目录缺少 app/'+entry+'，请重新完整解压。')
    for name in MODULES:
        try:
            available=importlib.util.find_spec(name) is not None
        except (ImportError, ModuleNotFoundError):
            available=False
        if not available: errors.append('缺少依赖：'+name)
    return errors

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
    errors=check(root)
    if errors:
        print('\n'.join(errors),file=sys.stderr)
        print('激活项目虚拟环境后执行：python -m pip install -r requirements-runtime.txt',file=sys.stderr)
        return 2
    if args.check:
        print('启动所需环境检查通过；不代表求解、界面或跨平台功能已全部验证。')
        return 0
    try: cmd=command(root, interpreter, args.port,args.profile)
    except ValueError as exc:
        print(str(exc),file=sys.stderr);return 2
    print('本机访问：http://127.0.0.1:'+str(args.port)+'；停止网页按 Ctrl+C。')
    env={**os.environ,'PYTHONUTF8':'1'}
    try: return subprocess.call(cmd,cwd=root,env=env)
    except KeyboardInterrupt: return 130

if __name__=='__main__': raise SystemExit(main())
