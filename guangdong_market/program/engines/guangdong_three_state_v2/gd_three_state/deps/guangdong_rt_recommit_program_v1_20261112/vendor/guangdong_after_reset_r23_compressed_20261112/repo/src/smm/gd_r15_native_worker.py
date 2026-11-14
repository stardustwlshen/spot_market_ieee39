"""One fresh bounded official HiGHS occurrence. Used only by reviewed R15 adapter."""
import json
import os
from pathlib import Path
import resource
import sys
from datetime import datetime, timezone
from hashlib import sha256


def file_hash(path):
    return sha256(Path(path).read_bytes()).hexdigest()


def now():
    return datetime.now(timezone.utc).isoformat()


def main(argv):
    request, response, names = map(Path, argv)
    resource.setrlimit(resource.RLIMIT_FSIZE, (16 * 1024 ** 2, 16 * 1024 ** 2))
    envelope = json.loads(request.read_text())
    from gd_r15_native_identity import runtime_identity
    runtime = runtime_identity()
    occurrence = dict(id=envelope['occurrence_id'], worker_pid=os.getpid(),
        request_sha256=file_hash(request), names_sha256=file_hash(names),
        source_tree_sha256=envelope['source_tree_sha256'], input_sha256=envelope['input_sha256'],
        worker_file_sha256=file_hash(__file__), started_at_utc=now(), native_run_count=0,
        runtime_identity=runtime)
    entered_engine = False
    try:
        if runtime != envelope['runtime_identity']:
            raise ValueError('R15 runtime identity mismatch')
        if envelope['schema'] != 'gd.research.r15.native_two_window.v1.request':
            raise ValueError('R15 request schema required')
        if occurrence['names_sha256'] != envelope['names_sha256'] or occurrence['worker_file_sha256'] != envelope['worker_file_sha256']:
            raise ValueError('R15 occurrence source/names mismatch')
        # Existing official transport receives its exact serialized mathematical payload.
        # No fallback, old permits, secondary pricing or preparation call is made.
        from highs_worker import solve
        entered_engine = True
        raw = solve(envelope['payload'])
        occurrence['native_run_count'] = 0 if raw.get('native_status') == 'deadline_before_native_run' else 1
    except Exception as exc:
        occurrence['native_run_count'] = None if entered_engine else 0
        raw = dict(version=1, error=f'{type(exc).__name__}: {exc}')
    occurrence['finished_at_utc'] = now()
    raw['occurrence'] = occurrence
    with response.open('x', encoding='utf-8') as f:
        json.dump(raw, f, sort_keys=True, separators=(',', ':'), allow_nan=False)


if __name__ == '__main__':
    main(sys.argv[1:])
