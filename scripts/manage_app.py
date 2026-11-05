"""Identity-checked POSIX developer background server management.

The cross-platform user launcher remains launch_app.py. Unverified or legacy
PID records never authorize signals. This manager never signals solvers.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from smm.live_runner import atomic_json
from smm.process_control import file_lock, process_identity, process_state


class ManagementError(RuntimeError):
    pass


def command_for(root, port, address):
    return [sys.executable, "-m", "streamlit", "run", str(root / "app/主页.py"),
            "--server.address", address, "--server.port", str(port),
            "--server.headless", "true", "--browser.gatherUsageStats", "false",
            "--server.fileWatcherType", "none"]


def read_record(root):
    path = root / "outputs/app-process.json"
    legacy = root / "outputs/app.pid"
    if not path.exists():
        if legacy.exists():
            raise ManagementError("只有旧版 app.pid，无法证明进程归属，未发送信号。请核实旧网页已关闭后备份该文件，再重试。")
        return None
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ManagementError("网页进程记录损坏或无法读取，未发送信号；请保留记录并人工核实。") from exc
    if (not isinstance(record, dict) or record.get("format") != 1
            or record.get("root") != str(root.resolve())
            or not isinstance(record.get("command"), list)):
        raise ManagementError("网页进程记录不属于本项目或格式无效，未发送信号。")
    expected = command_for(root, record.get("port"), record.get("address"))
    if record["command"] != expected:
        raise ManagementError("网页进程记录的解释器或命令不匹配本项目，未发送信号。")
    return record


def owned_process(record):
    """Require boot/machine/creation identity and the actual project command."""
    state = process_state(record.get("identity"))
    if state == "dead":
        return None
    if state != "alive":
        raise ManagementError("无法证明网页进程属于本次本机启动，未发送信号。")
    import psutil
    try:
        process = psutil.Process(record["identity"]["pid"])
        if (abs(process.create_time() - record["identity"]["create_time"]) > 1e-6
                or process.cmdline() != record["command"]):
            raise ManagementError("进程号已复用或实际命令不匹配，未发送信号。")
        return process
    except psutil.NoSuchProcess:
        return None
    except psutil.AccessDenied as exc:
        raise ManagementError("无权核实网页进程命令，未发送信号。") from exc


def stop_server(root):
    record = read_record(root)
    if record is None:
        print("没有本入口的网页进程记录。")
        return
    process = owned_process(record)
    if process is not None:
        # psutil additionally guards PID reuse between inspection and signal.
        import psutil
        try:
            process.terminate()
            process.wait(timeout=10)
        except psutil.NoSuchProcess:
            pass
        except psutil.TimeoutExpired as exc:
            raise ManagementError("网页进程未在10秒内停止；未强杀，保留原记录，请人工检查。") from exc
        except psutil.AccessDenied as exc:
            raise ManagementError("系统拒绝停止网页进程；保留原记录，未尝试强杀。") from exc
    (root / "outputs/app-process.json").unlink()
    (root / "outputs/app.pid").unlink(missing_ok=True)
    print("本入口网页服务已停止；后台求解未被强杀。")


def start_server(root, port, address):
    record = read_record(root)
    if record is not None and owned_process(record) is not None:
        print(f"已在运行：http://{record['address']}:{record['port']}")
        return
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind((address, port))
    except OSError as exc:
        raise ManagementError(f"监听地址或端口不可用：{address}:{port}；未停止其他程序。") from exc
    command = command_for(root, port, address)
    logfile = root / "outputs/app.log"
    with logfile.open("a", encoding="utf-8") as log:
        process = subprocess.Popen(command, cwd=root, stdout=log, stderr=subprocess.STDOUT,
                                   start_new_session=True)
    try:
        identity = process_identity(process.pid)
        record = dict(format=1, root=str(root.resolve()), command=command,
                      port=port, address=address, identity=identity)
        atomic_json(root / "outputs/app-process.json", record)
        (root / "outputs/app.pid").write_text(str(process.pid) + "\n", encoding="ascii")
    except BaseException:
        # This exact Popen child is ours even if metadata storage failed.
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                pass
        raise
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    host = "127.0.0.1" if address == "0.0.0.0" else address
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise ManagementError(f"网页启动失败（返回码 {process.returncode}），请查看 {logfile}。")
        try:
            with opener.open(f"http://{host}:{port}/_stcore/health", timeout=1) as response:
                healthy = response.status == 200 and response.read().strip() == b"ok"
            if healthy and process.poll() is None and owned_process(record) is not None:
                print(f"已启动：http://{host}:{port}（监听 {address}）；日志 {logfile}")
                return
        except (OSError, urllib.error.URLError):
            pass
        time.sleep(.2)
    stop_server(root)
    raise ManagementError(f"网页启动超时，已停止本次网页服务；请查看 {logfile}。")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("start", "stop", "restart"), nargs="?", default="start")
    args = parser.parse_args(argv)
    try:
        port = int(os.environ.get("PORT", "8501"))
        if not 1 <= port <= 65535:
            raise ValueError
    except ValueError:
        parser.error("PORT 须为1到65535之间的整数")
    address = os.environ.get("ADDR", "127.0.0.1")
    (ROOT / "outputs").mkdir(exist_ok=True)
    try:
        with file_lock(ROOT / "outputs/.app-control.lock"):
            if args.action in ("stop", "restart"):
                stop_server(ROOT)
            if args.action != "stop":
                start_server(ROOT, port, address)
        return 0
    except (ManagementError, OSError, RuntimeError) as exc:
        print(f"网页操作未完成：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
