"""Exactly one typed RT or PRICE continuous HiGHS call per owned process."""
import json
import os
from pathlib import Path
import resource
import sys
from datetime import datetime, timezone
from hashlib import sha256


def file_hash(path):return sha256(Path(path).read_bytes()).hexdigest()
def now():return datetime.now(timezone.utc).isoformat()


def main(argv):
    request,response,names=map(Path,argv)
    resource.setrlimit(resource.RLIMIT_FSIZE,(1024**2,1024**2))
    envelope=json.loads(request.read_text())
    from gd_r19_native_identity import runtime_identity
    runtime=runtime_identity();stage=envelope.get('stage')
    occurrence=dict(id=envelope['occurrence_id'],stage=stage,worker_pid=os.getpid(),
        request_sha256=file_hash(request),names_sha256=file_hash(names),
        source_tree_sha256=envelope['source_tree_sha256'],input_sha256=envelope['input_sha256'],
        policy_sha256=envelope['policy_sha256'],worker_file_sha256=file_hash(__file__),
        started_at_utc=now(),native_run_count=0,runtime_identity=runtime,
        window_index=envelope['window_index'],stage_ordinal=envelope['stage_ordinal'],run_id=envelope['run_id'],contract_sha256=envelope['contract_sha256'],window_policy_sha256=envelope['window_policy_sha256'],price_terminal_policy_sha256=envelope['price_terminal_policy_sha256'],formal_strict=envelope['formal_strict'])
    entered=False
    try:
        sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
        from smm.gd_r19_price_terminal import POLICY
        terminal_hash=sha256(json.dumps(POLICY,sort_keys=True,separators=(',', ':'),allow_nan=False).encode()).hexdigest()
        if envelope.get('price_terminal_policy_sha256')!=terminal_hash or envelope.get('formal_strict') is not False:raise ValueError('R19 research terminal policy required; formal strict mode unavailable')
        if runtime!=envelope['runtime_identity']:raise ValueError('runtime identity mismatch')
        if envelope['schema']!='gd.research.r19.priced_full_day.v1.request' or stage not in ('RT','PRICE'):raise ValueError('typed priced experiment request required')
        if occurrence['names_sha256']!=envelope['names_sha256'] or occurrence['worker_file_sha256']!=envelope['worker_file_sha256']:raise ValueError('worker/names mismatch')
        if type(envelope['window_index']) is not int or not 0<=envelope['window_index']<96 or envelope['stage_ordinal']!=2*envelope['window_index']+(stage=='PRICE'):raise ValueError('exact R19 stage ordinal required')
        expected=dict(threads=1,time_limit=120. if stage=='RT' else 60.,mip_rel_gap=.001,mip_abs_gap=0.,output_flag=False,presolve='choose',solve_relaxation=False)
        payload=envelope['payload']
        if payload['options']!=expected or any(v['kind']!='C' for v in payload['variables']) or not payload['objective']['minimize']:raise ValueError('exact continuous LP/options required')
        from highs_worker import solve
        entered=True;raw=solve(payload)
        occurrence['native_run_count']=0 if raw.get('native_status')=='deadline_before_native_run' else 1
    except Exception as exc:
        occurrence['native_run_count']=None if entered else 0
        raw=dict(version=1,error=f'{type(exc).__name__}: {exc}')
    occurrence['finished_at_utc']=now();raw['occurrence']=occurrence
    with response.open('x',encoding='utf-8') as f:json.dump(raw,f,sort_keys=True,separators=(',',':'),allow_nan=False)


if __name__=='__main__':main(sys.argv[1:])
