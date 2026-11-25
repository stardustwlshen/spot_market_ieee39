"""Single private container process; do not publish Streamlit without an auth proxy."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys
import tempfile

from launch_sichuan import check, command


def main(argv=None):
    parser=argparse.ArgumentParser(description='启动私有四川研究服务（不提供身份认证）')
    parser.add_argument('--check',action='store_true',help='验证依赖、端口参数和研究目录，不启动服务')
    parser.add_argument('--container',action='store_true',help='仅在隔离容器内监听全部网卡，由私有入口保护')
    args=parser.parse_args(argv)
    root=Path(__file__).resolve().parents[1]
    try:
        port=int(os.environ.get('PORT','8501'))
        cmd=command(root,sys.executable,port)
    except ValueError:
        print('PORT 必须是 1–65535 之间的整数。',file=sys.stderr)
        return 2
    errors=check(root)
    if errors:
        print('\n'.join(errors),file=sys.stderr)
        return 2
    data=root/'runs_sichuan_v5_consultation'
    try:
        data.mkdir(exist_ok=True)
        with tempfile.TemporaryFile(dir=data):
            pass
    except OSError as exc:
        print(f'四川研究档案目录不可写，请检查持久卷权限：{data}（{exc}）',file=sys.stderr)
        return 2
    if args.check:
        print('四川私有服务检查通过：依赖、HiGHS 和研究结果目录可用。')
        return 0
    if args.container:
        cmd[cmd.index('--server.address')+1]='0.0.0.0'
    cmd+=['--server.headless','true']
    os.chdir(root)
    os.execv(sys.executable,cmd)


if __name__=='__main__':
    raise SystemExit(main())
