"""Data-only SINGLE-window integration, not a day or execution runner.

Run: python -m smm.gd_october_window_job JOB.json NEW_ARCHIVE_DIRECTORY
No hardware, command issuance, pricing mapping, or settlement is performed.
Archive readback checks bytes only; it never reissues a native call receipt.
"""
import argparse
from dataclasses import fields, is_dataclass
from hashlib import sha256
import json
from importlib.metadata import distributions
import os
from pathlib import Path
import sys
from . import gd_october_job_codec as codec
from . import gd_october_native_solve as native
from .gd_october_roll import RollRequest, StateRecord, IssuedCommand, validate_prepared, _time
from .gd_october_rt_contract import validate_october_rt_contract

SCHEMA = 'gd.october.single_window_job.v1'
ARCHIVE_SCHEMA = 'gd.october.single_window_archive.v1'


def encode_job(inputs, request, *, mode, native_authorized):
    """All fields, including settings inside inputs, must be explicit in JSON."""
    return codec.dumps(dict(schema=SCHEMA, inputs=inputs, request=request,
                           mode=mode, native_authorized=native_authorized))


def _check_job(job):
    if type(job) is not dict or set(job) != {'schema','inputs','request','mode','native_authorized'}:
        raise ValueError('exact single-window job fields required')
    if job['schema'] != SCHEMA or job['mode'] not in ('prepare','build','native_highs'):
        raise ValueError('unknown job version or mode')
    if type(job['native_authorized']) is not bool: raise ValueError('explicit boolean authorization required')
    if job['mode'] == 'native_highs' and not job['native_authorized']:
        raise ValueError('native_highs requires explicit authorization')
    if type(job['inputs']) is not native.NativeWindowInputs or type(job['request']) is not RollRequest:
        raise ValueError('typed native inputs and request required')


def _prepare(inputs, request):
    if type(request.window_number) is not int or request.window_number < 0:
        raise ValueError('nonnegative integer window number required')
    for history, cls, key in ((request.available_observations,StateRecord,'state_id'),
                               (request.available_commands,IssuedCommand,'command_id')):
        if type(history) is not tuple or any(type(v) is not cls for v in history):
            raise ValueError('typed history tuple required')
        ids = [getattr(v,key) for v in history]
        if len(set(ids)) != len(ids): raise ValueError('duplicate history ID')
        for v in history:
            v.validate()
            if v.available_at > request.window.information_cutoff: raise ValueError('history unavailable at cutoff')
    observations = request.available_observations
    if any(v.kind == 'planned_projection' for v in observations): raise ValueError('projected history observation forbidden')
    if len({v.as_of for v in observations}) != len(observations): raise ValueError('ambiguous observation boundary')
    commands = request.available_commands
    if any(v.run_id != request.window.run_id for v in commands): raise ValueError('cross-run history command')
    intervals = [(v.delivery_start,v.delivery_end) for v in commands]
    if len(set(intervals)) != len(intervals): raise ValueError('duplicate history command interval')
    digest = validate_prepared(inputs.prepared, request)
    validate_static_inputs(window=inputs.prepared.window, settings=inputs.settings, publication=inputs.publication,
        bridge_inputs=inputs.bridge_inputs, known_dispatch_reference=inputs.known_dispatch_reference)
    return digest


def validate_static_inputs(*, window, settings, publication, bridge_inputs, known_dispatch_reference):
    """Shared static R6/R7 source/settings gate, independent of future state."""
    native._assert_real_durations(window)
    settings.validate(); publication.validate(window)
    b = bridge_inputs
    report = validate_october_rt_contract(b['contract_document'], **{k:b[k] for k in
        ('expected_identity','expected_delivery_start','asof','source_artifacts','mode')})
    if report['status'] != 'validated': raise ValueError('contract validation failed: ' + str(report['issues']))
    known = known_dispatch_reference.validate()
    contract = report['data']['contract']
    identity = b['expected_identity']
    if (known.run_id,known.window_id,known.dispatch_id,known.source_sha256) != tuple(identity[k] for k in
        ('run_id','window_id','selected_dispatch_id','selected_dispatch_sha256')):
        raise ValueError('known source identity mismatch')
    if contract['selected_dispatch_source'] != dict(source_id=known.source_id,source_sha256=known.source_sha256):
        raise ValueError('known source reference mismatch')
    if b['source_artifacts'][known.source_id] != known.artifact: raise ValueError('known source bytes mismatch')
    if _time(known.available_at,'known availability') > window.information_cutoff:
        raise ValueError('known source unavailable at cutoff')
    mapping = json.loads(b['source_artifacts'][contract['mapping_source']['source_id']])
    if _time(mapping['selected_dispatch_available_at'],'mapping availability') != _time(known.available_at,'known availability'):
        raise ValueError('known availability mismatch')
    ref = b['resource_mapping_ref']
    if set(ref) != {'source_id','source_sha256'} or sha256(b['source_artifacts'][ref['source_id']]).hexdigest() != ref['source_sha256']:
        raise ValueError('physical mapping source hash mismatch')


def _no_symlink(path):
    path = Path(path).absolute()
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('symlink paths are not permitted')


def _fsync_directory(path):
    # Windows does not support POSIX directory handles via os.open. Files still
    # flush+fsync; atomic replace is used, but directory crash durability differs.
    if os.name == 'nt':
        return
    fd = os.open(path, os.O_RDONLY)
    try: os.fsync(fd)
    finally: os.close(fd)


def _write(path, raw):
    _no_symlink(path)
    with path.open('xb') as f:
        f.write(raw); f.flush(); os.fsync(f.fileno())


def _json(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2).encode('utf-8')


def _source_files():
    # Whole application source tree, not a handpicked list of direct imports.
    _no_symlink(Path(__file__))
    root = Path(__file__).resolve().parents[1]
    _no_symlink(root)
    paths = sorted(root.rglob('*'))
    if any(p.is_symlink() for p in paths): raise ValueError('source tree symlink forbidden')
    return {p.relative_to(root).as_posix():p.read_bytes() for p in paths if p.is_file() and p.suffix == '.py'}


def _embedded(value, path='job'):
    if type(value) is bytes:
        yield path, value
    elif is_dataclass(value):
        for f in fields(value): yield from _embedded(getattr(value,f.name), path+'.'+f.name)
    elif type(value) in (tuple,list):
        for i,v in enumerate(value): yield from _embedded(v,path+'['+str(i)+']')
    elif type(value) is dict:
        for i,(k,v) in enumerate(value.items()):
            yield from _embedded(k,path+'.key['+str(i)+']')
            yield from _embedded(v,path+'.value['+str(i)+']')


def run_job(job_path, output_dir):
    """Persist failures and exact success bytes. Never overwrite an existing dir."""
    out = Path(output_dir)
    _no_symlink(out)
    out.mkdir(parents=False, exist_ok=False)
    report = dict(schema=ARCHIVE_SCHEMA, status='running', phase='read_job', scope='single_window_only',
        native_numerical_acceptance=False, solver_called=False, native_solve_attempted=False, input_prepared=False,
        model_built=False, preflight_passed=False, source_inventory_consistent=False,
        directory_fsync_supported=(os.name != 'nt'),
        execution_confirmed=False, command_issued=False,
        whole_day_feasibility_verified=False, official_algorithm_certified=False,
        mapping_status='unavailable_no_explicit_post_result_mapping', mapping=None,
        official_price=None, official_price_status='unavailable', settlement=None, settlement_status='unavailable')
    def checkpoint(phase):
        report['phase'] = phase
        temporary = out/'report.pending.json'
        _write(temporary,_json(report))
        os.replace(temporary,out/'report.json')
        _fsync_directory(out)
    checkpoint('read_job')
    selected = None
    try:
        _no_symlink(job_path)
        if not Path(job_path).is_file() or Path(job_path).stat().st_size > 64*1024*1024:
            raise ValueError('regular job file of at most 64 MiB required')
        raw = Path(job_path).read_bytes()
        _write(out/'job.original.json',raw)
        report['job_sha256'] = sha256(raw).hexdigest()
        checkpoint('source_inventory')
        before = _source_files()
        source_inventory = []
        (out/'source').mkdir()
        for name,data in before.items():
            target=out/'source'/name; target.parent.mkdir(parents=True,exist_ok=True)
            _write(target,data)
            source_inventory.append(dict(path=name,sha256=sha256(data).hexdigest(),size=len(data)))
        _write(out/'source_inventory.json',_json(source_inventory))
        report['python_version'] = sys.version
        _write(out/'runtime_packages.json', _json(sorted(
            [dict(name=d.metadata.get('Name','unknown'),version=d.version) for d in distributions()],
            key=lambda d:(d['name'],d['version']))))
        checkpoint('decode')
        job = codec.loads(raw); _check_job(job)
        report['mode'] = job['mode']
        from .gd_october_thermal import is_research_book
        report['research_thermal_assumptions']=is_research_book(job['inputs'].prepared.physics.thermal_trajectories)
        report['operational_source_verified']=False
        report['official_market_accepted']=False
        _write(out/'input_byte_inventory.json',_json([dict(location=p,sha256=sha256(b).hexdigest(),size=len(b))
                                                    for p,b in _embedded(job)]))
        checkpoint('prepare')
        inputs, request = job['inputs'], job['request']
        report['prepared_sha256'] = _prepare(inputs,request)
        report['input_prepared'] = True
        if job['mode'] != 'prepare':
            checkpoint('preflight')
            context = native.preflight_october_native_window(inputs,request=request)
            _write(out/'preflight.json',_json(context['before']))
            report['preflight_identity'] = context['before']
            report['model_built'] = report['preflight_passed'] = True
            if job['mode'] == 'native_highs':
                report['native_solve_attempted'] = True
                report['solver_called'] = None  # Unknown until an actual call receipt is available.
                checkpoint('native_highs')
                solution = native.solve_october_native_window(inputs,request=request)
                data,identity = native.validate_october_native_solution(solution,inputs.prepared,report['prepared_sha256'])
                provenance = solution.provenance
                if provenance['mock_flow_only'] or not provenance['native_numerical_acceptance'] or not provenance['solver_called']:
                    report['mock_flow_only'] = True
                    report['solver_called'] = False
                    _write(out/'rejected_mock_provenance.json',_json(provenance))
                    raise ValueError('native_highs rejects mock evidence; no native acceptance or selected artifact')
                if any(data[k] != context['before'][k] for k in
                       ('input_bundle_sha256','solve_intent_sha256','physical_model_sha256')):
                    raise ValueError('preflight identity changed before native solve')
                selected = (solution.artifact, provenance)
                report['selected_dispatch_sha256'] = solution.selected_dispatch_sha256
                report['solver_called'] = provenance['solver_called']
                report['mock_flow_only'] = provenance['mock_flow_only']
                report['backend'] = solution.backend
        checkpoint('source_recheck')
        if _source_files() != before: raise ValueError('application source changed during job')
        report['source_inventory_consistent'] = True
        if selected is not None:
            checkpoint('archive_selected')
            _write(out/'selected.json',selected[0])
            _write(out/'provenance.json',_json(selected[1]))
            report['native_numerical_acceptance'] = selected[1]['native_numerical_acceptance']
        report['status'] = 'completed'
        report['phase'] = 'complete'
    except Exception as exc:
        report['status'] = 'failed'
        report['native_numerical_acceptance'] = False
        report['error'] = dict(type=type(exc).__name__,message=str(exc))
    finally:
        checkpoint(report['phase'])
        inventory={p.relative_to(out).as_posix():dict(sha256=sha256(p.read_bytes()).hexdigest(),size=p.stat().st_size)
                   for p in sorted(out.rglob('*')) if p.is_file()}
        _write(out/'manifest.json',_json(dict(schema=ARCHIVE_SCHEMA,files=inventory)))
        _fsync_directory(out)
    return report


def verify_archive(output_dir):
    """Hash-only, unsigned integrity readback. NEVER native acceptance/receipt."""
    out=Path(output_dir)
    _no_symlink(out)
    if any(p.is_symlink() for p in out.rglob('*')): raise ValueError('archive symlink forbidden')
    manifest=json.loads((out/'manifest.json').read_bytes(),object_pairs_hook=codec._pairs)
    if set(manifest) != {'schema','files'} or manifest['schema'] != ARCHIVE_SCHEMA:
        raise ValueError('unknown archive version')
    actual={p.relative_to(out).as_posix() for p in out.rglob('*') if p.is_file()}
    if actual != set(manifest['files']) | {'manifest.json'}: raise ValueError('archive file inventory mismatch')
    for name,expected in manifest['files'].items():
        p=Path(name)
        if p.is_absolute() or '..' in p.parts or (out/p).is_symlink(): raise ValueError('unsafe archive path')
        raw=(out/p).read_bytes()
        if expected != dict(sha256=sha256(raw).hexdigest(),size=len(raw)): raise ValueError('archive hash mismatch: '+name)
    return dict(status='hashes_verified_only',native_numerical_acceptance=False,
                receipt_restored=False,source_authenticity_verified=False)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('job'); parser.add_argument('output_dir')
    args=parser.parse_args(argv)
    report=run_job(args.job,args.output_dir)
    print(json.dumps(report,allow_nan=False))
    return 0 if report['status']=='completed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
