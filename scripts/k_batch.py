"""Run isolated-output comparisons without rewriting input files or invoking Git.

No solver runs during --dry-run. Negative-price input is required unless the
caller explicitly requests --skip-negative. Every real batch has unique output
tags and a manifest; an old summary.json is never treated as a fresh success.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading

ROOT = Path(__file__).resolve().parents[1]


def job_specs(negative_bids: Path | None) -> list[tuple[str, list[str]]]:
    jobs = [
        ("nops", ["--bid-version", "1", "--no-pumped"]),
        ("anchor_2h", ["--bid-version", "1", "--storage-mode", "sced_anchor", "--anchor", "two_hour_eq"]),
        ("anchor_hourly", ["--bid-version", "1", "--storage-mode", "sced_anchor", "--anchor", "hourly_eq"]),
        ("anchor_pct", ["--bid-version", "1", "--storage-mode", "sced_anchor", "--anchor", "window_end_pct"]),
        ("fixed_da", ["--bid-version", "1", "--storage-mode", "fixed_da"]),
        ("fallback", ["--bid-version", "1", "--band-fraction", "0"]),
        ("intraday", ["--bid-version", "1", "--intraday"]),
        ("nores", ["--bid-version", "1", "--no-storage-reserve"]),
        ("bid_v3", ["--bid-version", "3"]),
    ]
    if negative_bids is not None:
        jobs.append(("negprice", ["--bid-file", str(negative_bids)]))
    return jobs


def source_hashes(root: Path) -> dict[str, str]:
    """Record this batch's actual sources, not an older package's hash claim."""
    result = {}
    for folder in ("config", "data", "bids", "src", "scripts"):
        for path in sorted((root / folder).rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc":
                result[str(path.relative_to(root))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workers", type=int, nargs="?", default=3)
    parser.add_argument("--negative-bids", type=Path,
                        default=ROOT / "outputs/k_bids/bids_v901_negprice.json")
    parser.add_argument("--skip-negative", action="store_true", help="明确跳过缺少输入的负价场景，报告中标为未验证")
    parser.add_argument("--log-root", type=Path, default=ROOT / "outputs/k_batches")
    parser.add_argument("--dry-run", action="store_true", help="仅检查输入并打印任务，不写文件、不启动求解")
    args = parser.parse_args(argv)
    if args.workers < 1:
        parser.error("workers 必须为正整数")
    negative = None if args.skip_negative else args.negative_bids.resolve()
    if negative is not None:
        if not negative.is_file():
            parser.error(f"负价报价文件不存在：{negative}；请提供 --negative-bids，或明确 --skip-negative")
        try:
            bids = json.loads(negative.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            parser.error(f"负价报价文件无法读取：{exc}")
        if not isinstance(bids, dict) or not isinstance(bids.get("units"), dict):
            parser.error("负价报价 JSON 必须为包含 units 对象的完整报价；具体报价规则由出清入口继续校验")
    jobs = job_specs(negative)
    skipped = ["negprice: 用户显式跳过，未验证"] if args.skip_negative else []
    if args.dry_run:
        print(json.dumps({"jobs": jobs, "skipped": skipped, "read_only_inputs": True}, ensure_ascii=False, indent=2))
        return 0
    args.log_root.mkdir(parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix="batch_", dir=args.log_root)).resolve()
    manifest = dict(batch=directory.name, python=sys.executable, root=str(ROOT), skipped=skipped,
                    input_files_sha256=source_hashes(ROOT), read_only_inputs=True,
                    hash_scope="before_launch_and_after_completion;not_a_frozen_input_snapshot",
                    package_manifest_status="historical_only_not_used_as_current_integrity_certificate", jobs=[])
    if negative is not None:
        manifest["negative_bids_sha256"] = hashlib.sha256(negative.read_bytes()).hexdigest()
    manifest_path = directory / "batch_manifest.json"

    def save():
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    save()
    active, lock, cancelled = {}, threading.Lock(), threading.Event()

    def run(job):
        name, extra = job
        tag = f"_k_{name}_{directory.name}"
        command = [sys.executable, str(ROOT / "scripts/main_full.py"), "--no-plots",
                   "--read-only-inputs", *extra, "--tag", tag]
        record = dict(name=name, command=command, output_tag=tag, log=str(directory / f"{name}.log"))
        try:
            with Path(record["log"]).open("w", encoding="utf-8") as log:
                with lock:
                    if cancelled.is_set():
                        return dict(record, returncode=None, status="cancelled_before_start")
                    process = subprocess.Popen(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
                    active[name] = process
                code = process.wait()
                return dict(record, returncode=code, status="process_succeeded" if code == 0 else "process_failed")
        except OSError as exc:
            return dict(record, returncode=None, status="launch_failed", error=str(exc))
        finally:
            with lock:
                active.pop(name, None)

    pool = ThreadPoolExecutor(max_workers=args.workers)
    futures = [pool.submit(run, job) for job in jobs]
    interrupted = False
    try:
        for future in as_completed(futures):
            manifest["jobs"].append(future.result())
            save()
    except KeyboardInterrupt:
        interrupted = True
        cancelled.set()
        with lock:
            owned = list(active.values())
        for process in owned:
            if process.poll() is None:
                try:
                    process.terminate()
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                except ProcessLookupError:
                    pass
    finally:
        pool.shutdown(wait=True)
        manifest["jobs"] = [future.result() for future in futures]
        manifest["interrupted"] = interrupted
        manifest["all_processes_succeeded"] = not interrupted and all(x["status"] == "process_succeeded" for x in manifest["jobs"])
        manifest["input_files_sha256_after"] = source_hashes(ROOT)
        manifest["source_inputs_unchanged"] = manifest["input_files_sha256_after"] == manifest["input_files_sha256"]
        if negative is not None:
            manifest["negative_bids_sha256_after"] = hashlib.sha256(negative.read_bytes()).hexdigest() if negative.is_file() else None
            manifest["source_inputs_unchanged"] &= manifest["negative_bids_sha256_after"] == manifest["negative_bids_sha256"]
        manifest["validation_note"] = "进程退出成功不等于模型验收通过；仍须核对实际运行结果。"
        save()
    print(f"批次记录：{manifest_path}")
    return 130 if interrupted else (0 if manifest["all_processes_succeeded"] and manifest["source_inputs_unchanged"] else 1)


if __name__ == "__main__":
    raise SystemExit(main())
