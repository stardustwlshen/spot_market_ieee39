"""File-only hourly persistence barrier. Never grants math or safety authority.

The coordinator uploads pack()'s exact archive through Library and then calls
acknowledge() with the returned Library ID. The worker never calls cloud APIs.
"""
from pathlib import Path
import argparse, gzip, hashlib, json, os, tarfile, time, re
from datetime import datetime, timedelta
from decimal import Decimal

def read(p): return json.loads(Path(p).read_bytes())
def sha(p):
    h = hashlib.sha256()
    with Path(p).open('rb') as f:
        for b in iter(lambda: f.read(1024 * 1024), b''): h.update(b)
    return h.hexdigest()
def need(v, message):
    if not v: raise ValueError(message)
def atomic(p, obj):
    p = Path(p); need(not p.exists(), 'immutable record already exists: ' + str(p))
    tmp = p.with_name(p.name + '.writing')
    with tmp.open('x') as f:
        json.dump(obj, f, ensure_ascii=False, indent=2); f.flush(); os.fsync(f.fileno())
    tmp.rename(p)
    fd = os.open(p.parent, os.O_RDONLY | os.O_DIRECTORY)
    try: os.fsync(fd)
    finally: os.close(fd)
def canonical(p): return str(Path(p).resolve(strict=True))
def pinned(p, expected): need(sha(p) == expected, 'source changed: ' + str(p))
def scan_pins(obj):
    if isinstance(obj, dict):
        historical = {'old_source_missing','historical_context'} if obj.get('schema') == 'gd.PRICE.reaccepted_preserved_raw.v1' else set()
        # These two fields describe explicitly LOST old evidence, not current
        # dependencies. The proof file itself is still archived without edits.
        # Typed siblings such as qN_SOURCE.json are outside qN/ and are not
        # necessarily repeated in source_pins. Descriptor stored hashes bind
        # on-disk compressed bytes; raw_sha256 must not be substituted.
        for key, value in obj.items():
            if key in historical: continue
            if not (isinstance(value,str) and value.startswith('/')): continue
            candidates = [key+'_sha256']
            if key.endswith('_path'): candidates.append(key[:-5]+'_sha256')
            if key in ('path','file','source'): candidates += ['stored_sha256','sha256']
            expected = next((obj[k] for k in candidates if isinstance(obj.get(k),str)
                             and len(obj[k])==64), None)
            if expected is not None: yield value, expected
        for key, value in obj.items():
            if key in historical: continue
            if key == 'source_pins' and isinstance(value, dict):
                for p, h in value.items():
                    need(isinstance(p, str) and isinstance(h, str) and len(h) == 64,
                         'malformed source pin')
                    yield p, h
            else: yield from scan_pins(value)
    elif isinstance(obj, list):
        for value in obj: yield from scan_pins(value)

def import_mapper(config):
    """Bind a verified store once per operation; each mapped read still hashes bytes."""
    descriptor=config.get('historical_import')
    store=None
    if descriptor:
        from historical_store import HistoricalStore
        store=HistoricalStore(descriptor['manifest'],descriptor['manifest_sha256'])
    def mapped(path,expected=None):
        if store is not None and str(path) in store.data['map']:
            digest,record=store._record(str(path),expected)
            store.bytes(str(path),digest)
            return Path(record['path'])
        return Path(path)
    return mapped

def inventory(config, output, hour, *, checkpoint_kind="hour"):
    """Baseline and already cloud-acknowledged hours, not hash-only new data."""
    pinned(config['baseline_manifest'], config['baseline_manifest_sha256'])
    base = read(config['baseline_manifest'])
    if config['policy'] == 'cloud_required':
        need(base.get('library_dependencies'), 'verified baseline Library references required')
    mapped=import_mapper(config)
    known = dict(base['files']); dependencies = list(base['library_dependencies'])
    history_root=Path(config.get('checkpoint_root',output))
    candidates = list((history_root / 'DURABLE_HOURS').glob('H*/READY.json'))
    if checkpoint_kind == 'quarter': candidates += list((history_root / 'DURABLE_QUARTERS').glob('Q*/READY.json'))
    for prior in sorted(candidates, key=lambda p: read(p)['last_window']):
        z = read(prior)
        boundary = hour if checkpoint_kind == 'quarter' else 4*hour
        if z['last_window'] >= boundary: continue
        need(z['policy'] == config['policy'], 'checkpoint policy cannot change inside an attempt')
        if config['policy'] == 'cloud_required':
            receipt = validate_receipt(prior.parent, local_archive=False)
            dependencies.append(dict(library_file_id=receipt['library_file_id'],
                                     archive_sha256=receipt['archive_sha256'], hour=z['hour']))
        else:
            archive = read(prior.parent / 'ARCHIVE.json')
            pinned(prior, archive['manifest_sha256']); pinned(archive['path'], archive['archive_sha256'])
            dependencies.append(dict(local_archive=archive['path'], archive_sha256=archive['archive_sha256'],
                                     hour=z['hour'], cloud_persisted=False))
        for item in z['files']:
            known[item['restore_path']] = item['sha256']
    # Accept byte-identical recorded aliases, but never conflicting paths.
    normalized = {}
    for p, h in known.items():
        key = canonical(mapped(p,h))
        need(key not in normalized or normalized[key] == h, 'baseline alias conflict')
        normalized[key] = h
    # Retain exact declared aliases as well, so repeated old source_pins are
    # resolved once per hourly catalogue rather than once per JSON occurrence.
    for p,h in known.items():
        need(p not in normalized or normalized[p] == h, 'baseline declared-path conflict')
        normalized[p] = h
    return normalized, dependencies

def preflight_config(output, config_path, expected_config_sha256, *, normal_runtime=False):
    output = Path(output).resolve(); pinned(config_path, expected_config_sha256)
    config = read(config_path);mapped=import_mapper(config)
    need(config.get('policy') in ('cloud_required', 'local_only'), 'explicit persistence policy required')
    need(config.get('execution_attempt_id') == output.name, 'configuration belongs to another attempt')
    need(output.is_relative_to(Path(config['persistent_root']).resolve()), 'output outside persistent root')
    if normal_runtime and 'checkpoint_root' in config:
        need(False, 'normal resume does not support explicit checkpoint_root; omit it to use the default output root; explicit roots are retrospective serialization only')
    if 'checkpoint_root'in config:
        need(Path(config['checkpoint_root']).resolve().is_relative_to(Path(config['persistent_root']).resolve()),'checkpoint_root outside persistent root')
    pinned(config['baseline_manifest'], config['baseline_manifest_sha256'])
    base = read(config['baseline_manifest'])
    need(isinstance(base['files'], dict), 'baseline file map required')
    previous=config.get('previous_execution_ledger')
    if previous is not None:
        need(set(previous)=={'path','sha256'},'explicit previous ledger descriptor required')
        pinned(previous['path'],previous['sha256'])
        target=canonical(previous['path']);matches={h for p,h in base['files'].items()if canonical(mapped(p,h))==target}
        need(matches=={previous['sha256']},'previous execution ledger not in exact persisted baseline')
        ledger=read(previous['path']);need(ledger['schema']=='gd.executed_quarter.accounting_commit.v1',
            'unsupported previous execution ledger')
    if config['policy'] == 'cloud_required':
        need(base.get('library_dependencies'), 'cloud baseline references required')
    return config

def validate_hour(output, hour):
    need(type(hour) is int and 0 <= hour < 24, 'invalid hour')
    roots = []; occurrences = set()
    for w in range(hour * 4, hour * 4 + 4):
        folder = output / 'pipeline/dispatch' / ('W' + str(w)); roots.append(folder)
        for name in ('RESULT.json', 'EXACT_CARRY.json', 'BEFORE_HANDOFF.json',
                     'prepared/SUMMARY.json', 'actual/ACCEPTED_PAIR.json'):
            need((folder / name).is_file(), 'incomplete physical handoff: ' + str(folder / name))
        pair = read(folder / 'actual/ACCEPTED_PAIR.json')
        need(pair['status'] == 'accepted_research_pair' and pair.get('safety_release') is False,
             'physical acceptance missing or unsafe release claim')
        for stage in ('SCUC', 'SCED'):
            d = folder / 'actual' / stage
            for name in ('JOB.json', 'COMPLETED.json', 'request.json.gz', 'response.json.gz'):
                need((d / name).is_file(), 'unknown native outcome: ' + str(d))
            key = canonical(d); need(key not in occurrences, 'duplicate native occurrence')
            occurrences.add(key)
            need(read(d / 'COMPLETED.json')['solve_entries'] == 1, 'native count not one')
            pinned(d / 'response.json.gz', pair[stage + '_response_sha256'])
    for q in range(hour * 4, hour * 4 + 4):
        folder = output / 'pipeline/price' / ('q' + str(q)); roots.append(folder)
        z = read(folder / 'ACCEPTED.json'); d = folder / 'prepared/native_once'
        need(z['context']['day_quarter'] == q and z['KKT']['passed'] is True,
             'price not accepted for required quarter')
        for name in ('LAUNCH.json', 'IDENTITY.json', 'COMPLETED.json', 'request.json', 'response.json'):
            need((d / name).is_file(), 'unknown price native outcome: ' + str(d))
        key = canonical(d); need(key not in occurrences, 'duplicate price occurrence')
        occurrences.add(key)
        pinned(d / 'response.json', read(d / 'COMPLETED.json')['response_sha256'])
    h = read(output / 'pipeline/price' / ('HOUR' + str(hour) + '.json'))
    need(h['input_binding']['hour'] == hour and h['executed_cells'] == 4 and
         h.get('payable') is False and h.get('safety_release') is False,
         'hour missing or claims formal/safe payment')
    if read(output / 'PLAN.json').get('finite_hourly'):
        due = output / 'pipeline/dispatch' / ('W'+str(hour*4+2))
        receipt = read(due / ('H%d_RESEARCH_AGC_RECEIPT.json' % (hour+1)))
        selected = receipt['selection']
        need(receipt['hour_index'] == hour+1 and selected['handoff_complete'] is True and
             selected['safety_release'] is False and selected['formal'] is False,
             'next-hour selected receipt incomplete or claims formal safety')
        pinned(due / ('H%d_AGC_MATH_CANDIDATE.json' % (hour+1)), selected['candidate_sha256'])
        pinned(due / 'EXACT_CARRY.json', selected['carry_sha256'])
        binding = read(output / 'pipeline/dispatch' / ('W'+str(hour*4+3)) / 'AGC_BINDING.json')
        def applied(obj):
            if isinstance(obj, dict):
                for k,v in obj.items():
                    if k == 'applied_receipt_hashes': yield from v
                    else: yield from applied(v)
            elif isinstance(obj,list):
                for v in obj: yield from applied(v)
        need(receipt['receipt_digest'] in set(applied(binding)), 'selected receipt not bound in following plan')
    next_q = (hour + 1) * 4
    if next_q < 96:
        need(not (output / 'pipeline/price' / ('q'+str(next_q)) / 'prepared/native_once').exists(),
             'stable checkpoint must precede next price native call')
    if hour == 23:
        for name in ('POST_D1_REFERENCE', 'D1_REFERENCE'):
            d = output / 'pipeline' / name
            if d.exists():
                need((d/'ACCEPTED_REFERENCE.json').is_file(), 'terminal reference incomplete')
                for f in ('JOB.json','COMPLETED.json','request.json.gz','response.json.gz'):
                    need((d/'actual/SCED'/f).is_file(), 'terminal reference raw evidence incomplete')
                roots.append(d)
    return roots

def seal_hour(output, hour, config_path, expected_config_sha256):
    output = Path(output).resolve(); config_path = Path(config_path)
    config = preflight_config(output, config_path, expected_config_sha256)
    need(output.is_relative_to(Path(config['persistent_root']).resolve()),
         'new output must live under persistent workspace, not tmpfs')
    roots = validate_hour(output, hour)
    attempt = read(output / 'PLAN.json').get('execution_attempt_id')
    need(isinstance(attempt, str) and attempt and all(c.isalnum() or c in '-_' for c in attempt),
         'explicit execution attempt required')
    need(attempt == config['execution_attempt_id'], 'PLAN/config attempt mismatch')
    if (output / 'RESUME_PLAN.json').exists():
        need(read(output / 'RESUME_PLAN.json')['execution_attempt_id'] == attempt, 'attempt mismatch')
    extras = []
    for stem in ('HOUR', 'EXECUTION_H', 'SETTLEMENT_H', 'FOUR_PRICE_ARITHMETIC_INPUTS_H'):
        p = output / 'pipeline/price' / (stem + str(hour) + '.json')
        need(p.is_file(), 'hour evidence absent: ' + str(p)); extras.append(p)
    return _seal_selected(output, config_path, expected_config_sha256, config, roots, extras,
                          'hour', hour, dict(hour=hour, last_window=hour*4+3,
                          next_price_quarter=hour*4+4))

def _seal_selected(output, config_path, expected_config_sha256, config, roots, extras,
                   checkpoint_kind, index, fields):
    attempt = read(output / 'PLAN.json')['execution_attempt_id']
    parent, stem = ('DURABLE_HOURS','H') if checkpoint_kind == 'hour' else ('DURABLE_QUARTERS','Q')
    directory = Path(config.get('checkpoint_root',output)).resolve() / parent / (stem+'%02d' % index)
    directory.mkdir(parents=True, exist_ok=False)
    known, dependencies = inventory(config, output, index, checkpoint_kind=checkpoint_kind)
    if checkpoint_kind=='quarter':
        ledger=execution_ledger(output,index,fields,config,directory)
        atomic(directory/'EXECUTION_LEDGER.json',ledger);extras=[*extras,directory/'EXECUTION_LEDGER.json']
    selected = {}; queue = [];mapped=import_mapper(config)
    def add(p, expected=None, restore=None, force=False):
        if not force and expected is not None and known.get(str(p)) == expected: return
        p = mapped(p,expected).resolve(strict=True); key = str(p)
        if not force and expected is not None and known.get(key) == expected: return
        h = sha(p)
        if expected is not None: need(h == expected, 'dependency changed: ' + key)
        if not force and known.get(key) == h: return
        target = restore or key
        if target in selected: need(selected[target]['sha256'] == h, 'conflicting restore destination')
        else:
            selected[target] = dict(source_path=key, restore_path=target,
                                 sha256=h, size=p.stat().st_size)
            if p.suffix == '.json': queue.append(p)
    for root in roots:
        for folder, dirs, files in os.walk(root, followlinks=True):
            dirs[:] = [d for d in dirs if d != '__pycache__']
            for name in files:
                if not name.endswith('.pyc'): add(Path(folder) / name)
    for p in extras: add(p, force=True)
    add(config_path); add(config['baseline_manifest']); add(Path(__file__).resolve())
    # Capture mutable status only while the worker is synchronously at this boundary.
    # These are actual captured bytes, not reconstructed or invented old records.
    snapshots = directory / 'snapshots'; snapshots.mkdir()
    for rel in ('PLAN.json', 'RESUME_PLAN.json', 'CHECKPOINT.json', 'pipeline/dispatch/RESULT.json'):
        p = output / rel
        if p.is_file():
            dest = snapshots / rel.replace('/', '__'); data = p.read_bytes()
            with dest.open('xb') as f: f.write(data); f.flush(); os.fsync(f.fileno())
            add(dest, restore=str(p), force=True)
    # Complete new transitive sources. Never omit a new result merely because its hash is known.
    while queue:
        obj = read(queue.pop())
        for p, h in scan_pins(obj): add(p, expected=h)
    files = sorted(selected.values(), key=lambda z: z['restore_path'])
    for i, item in enumerate(files): item['member'] = 'payload/%06d' % i
    z = dict(schema='gd.durable.hour.v1' if checkpoint_kind=='hour' else 'gd.durable.quarter.v1',
             checkpoint_kind=checkpoint_kind, output=str(output), execution_attempt_id=attempt, **fields,
             files=files, library_dependencies=dependencies,
             config_sha256=expected_config_sha256, require_download_ack=config.get('require_download_ack',False), native_calls=0,
             policy=config['policy'], safety_release=False, formal_bill=None,
             status='awaiting_cloud_persistence' if config['policy']=='cloud_required' else 'local_not_cloud_persisted')
    atomic(directory / 'READY.json', z)
    return directory

def execution_ledger(output, window, fields, config, directory):
    state=read(fields['executed_projection']);end=window+1;hours=[];components={};pins={}
    for h in range((window+1)//4):
        f=output/'pipeline/price'/('HOUR'+str(h)+'.json');z=read(f)
        need(z['input_binding']['hour']==h and z['executed_cells']==4 and z['payable']is False and
             z['formal_bill']is None and z['safety_release']is False,'invalid cumulative research hour')
        subtotal=Decimal(0)
        for row in z['rows']:
            subtotal+=Decimal(row['energy_amount_CNY'])
            for key,value in row['components_CNY'].items():components[key]=components.get(key,Decimal(0))+Decimal(value)
        need(abs(subtotal-Decimal(z['total_energy_component_CNY']))<=Decimal('0.000001'),'hour total mismatch')
        pins[str(f)]=sha(f);hours.append(dict(hour=h,path=str(f),sha256=sha(f),energy_component_CNY=str(subtotal)))
    prior=[p for p in directory.parent.glob('Q*/READY.json')if read(p)['window']<window]
    previous=max(prior,key=lambda p:read(p)['window'])if prior else None
    previous_ledger_path=None;previous_ledger_hash=None;prefix_basis='legacy_prefix_derived_from_completed_handoffs_and_HOUR'
    if previous:
        need(read(previous)['window']==window-1,'quarter checkpoint gap; explicit baseline needed')
        previous_ledger_path=previous.parent/'EXECUTION_LEDGER.json';previous_ledger_hash=sha(previous_ledger_path)
        prefix_basis='prior_quarter_checkpoint'
    elif config.get('previous_execution_ledger'):
        previous_ledger_path=Path(config['previous_execution_ledger']['path']);previous_ledger_hash=config['previous_execution_ledger']['sha256']
        pinned(previous_ledger_path,previous_ledger_hash);prefix_basis='externally_persisted_previous_attempt_ledger'
    else:need(fields['retrospective']or window==0,'continuation requires externally persisted previous execution ledger')
    if previous_ledger_path:
        prior_ledger=read(previous_ledger_path)
        need(prior_ledger.get('rt_clock_mode')=='provincial_15min_execution' and prior_ledger.get('grid_minutes')==15,'old grid accounting ledger cannot be resumed')
        need(prior_ledger['branch_id']==state['branch_id']and prior_ledger['window']==window-1 and
             prior_ledger['next_execute_tick']==window,'previous executed interval cannot be repeated or skipped')
        current_by_hour={h['hour']:h for h in hours}
        for old in prior_ledger['settled_hours']:
            need(old['hour']in current_by_hour and old['sha256']==current_by_hour[old['hour']]['sha256'],
                 'previous settled research hour changed or removed')
        pins[str(previous_ledger_path)]=previous_ledger_hash
    return dict(prefix_basis=prefix_basis,previous_execution_ledger_path=str(previous_ledger_path)if previous_ledger_path else None,
                previous_execution_ledger_sha256=previous_ledger_hash,schema='gd.executed_quarter.accounting_commit.v1',rt_clock_mode='provincial_15min_execution',grid_minutes=15,branch_id=state['branch_id'],window=window,
                commit_key=state['idempotency_key'],included_ticks=state['executed_cells'],next_execute_tick=end,
                previous_checkpoint_sha256=sha(previous)if previous else None,
                initial_external_baseline_sha256=config['baseline_manifest_sha256']if previous is None else None,
                settled_hour_ids=[h['hour']for h in hours],settled_hours=hours,
                settled_end_exclusive=4*len(hours),unsettled_executed_ticks=list(range(4*len(hours),end)),
                research_energy_components_CNY={k:str(v)for k,v in components.items()},
                research_energy_total_CNY=str(sum((Decimal(h['energy_component_CNY'])for h in hours),Decimal(0))),
                cumulative_complete=False if end%4 else True,
                accumulation='absolute sum of unique source-bound research HOUR records; not repeated delta application',
                missing_formal_inputs_not_zero=True,formal_bill=None,payable=False,safety_release=False,
                source_pins=pins,native_calls=0)


def validate_executed_projection(state, output, window, folder, incoming, carry):
    from .same_period import enabled,validate as validate_same_period
    if enabled(read(output/'PLAN.json')):return validate_same_period(state,output,window)
    need(state.get('schema')=='gd.executed_quarter.readonly_evidence.v1', 'explicit executed-state schema required')
    need(state['window']==window and state['new_native_calls']==0 and state['KKT_replayed']is False and
         state['safety_release']is False and state['formal_bill']is None,'invalid execution projection scope')
    result=read(folder/'RESULT.json');begin=window*3;end=begin+3
    need(state['branch_id']==result['branch_id']and state['executed_cells']==list(range(begin,end)),
         'executed projection wrong branch/cells')
    boundary=state['executed_boundary'];at=(datetime.fromisoformat(incoming['origin'])+timedelta(minutes=5*end)).isoformat()
    need(boundary['tick']==end and boundary['at']==at,'executed boundary clock mismatch')
    for key in ('thermal','thermal_age_h','storage','notice_values','notice_requirements','fixed_curve_state'):
        if key in incoming:need(boundary['state'].get(key)==incoming[key], 'executed boundary not incoming accepted state: '+key)
    ic=state['issued_carry'];need(Path(ic['path']).resolve()==(folder/'EXACT_CARRY.json').resolve(), 'issued carry identity')
    pinned(ic['path'],ic['sha256']);need(ic['executed_end']==end and ic['issued_end']==end+3 and
         ic['not_executed_range']==[end,end+3],'issued prefix mislabeled executed')
    if window:
        previous=output/'pipeline/dispatch'/('W'+str(window-1));names=previous/'prepared/names.json';raw=previous/'actual/SCED/response.json.gz'
        need(read(previous/'EXACT_CARRY.json')==incoming,'previous issued carry differs from executed boundary')
    else:
        run=Path(read(output/'PLAN.json')['run']);warm=read(run/'seed/WARMSTART_SOURCE.json');names=Path(warm['names']);raw=Path(warm['response'])
    need(Path(state['source_names']).resolve()==names.resolve() and Path(state['source_raw_response']).resolve()==raw.resolve(),
         'execution projection not exact previous-prefix source')
    pinned(raw,state['source_raw_file_sha256'])
    for path,h in state['source_pins'].items():pinned(path,h)
    normalized={canonical(p):h for p,h in state['source_pins'].items()}
    for f in (names,raw,folder/'prepared/CARRY.json',folder/'RESULT.json',folder/'EXACT_CARRY.json'):
        need(normalized.get(canonical(f))==sha(f),'execution projection missing exact source binding')
    encoded=raw.read_bytes();response=json.loads(gzip.decompress(encoded)if raw.suffix=='.gz'else encoded)
    n=read(names)['variables'];need(len(n)==len(response['primal']) and len(set(n))==len(n),'execution raw dimensions')
    expected={}
    for key,v in zip(n,response['primal']):
        match=re.search(r'(\d+)\]$',key)
        if match and int(match[1])in (0,1,2):expected[key[:match.start(1)]+str(begin+int(match[1]))+']']=v
    need(state['execution_values']==expected,'execution values not actual first three issued cells')
    identity=hashlib.sha256(json.dumps(dict(branch=result['branch_id'],window=window,first=begin,end=end,raw=sha(raw)),sort_keys=True).encode()).hexdigest()
    need(state['idempotency_key']==identity and state['available_price_quarter']==window and
         state['next_price_quarter_not_execution']==window+1,'execution accounting identity mismatch')
    need(state['accounting']['included_ticks']==list(range(begin,end)),'accounting includes wrong/future ticks')
    rc=state.get('resume_contract');need(isinstance(rc,dict),'explicit pending-issued resume contract required')
    need(rc['logical_executed_end_exclusive']==end and rc['next_window']==window+1 and
         rc['pending_issued_cells']==list(range(end,end+3))and rc['pending_source_window']==window and
         rc['next_model_cutoff_tick']==end and rc['next_optimization_delivery_tick']==end+3 and
         rc['expected_next_handoff_executed_end_exclusive']==end+3 and
         rc['predicted_optimization_initial_state_is_not_current_execution']is True,'pending issued clocks invalid')
    nr=folder/'prepared/names.json';rr=folder/'actual/SCED/response.json.gz'
    need(Path(rc['pending_source_names']).resolve()==nr.resolve()and Path(rc['pending_source_raw']).resolve()==rr.resolve(),
         'pending issued source identity invalid')
    pinned(nr,rc['pending_source_names_sha256']);pinned(rr,rc['pending_source_raw_sha256'])
    names=read(nr)['variables'];raw_response=json.loads(gzip.decompress(rr.read_bytes()))
    need(len(names)==len(raw_response['primal'])and len(set(names))==len(names),'pending issued raw dimensions')
    expected_pending={}
    for key,v in zip(names,raw_response['primal']):
        match=re.search(r'(\d+)\]$',key)
        if match and int(match[1])in (0,1,2):expected_pending[key[:match.start(1)]+str(end+int(match[1]))+']']=v
    need(rc['pending_issued_values']==expected_pending,'pending issued values not current accepted first prefix')
    need(rc['consume_pending_once_key']==dict(branch=result['branch_id'],period=window+1,first_tick=end,
         end_exclusive=end+3,raw_sha256=sha(rr)),'pending consumption identity changed')


def seal_quarter(output, window, config_path, expected_config_sha256, *, executed_state_path,
                 retrospective=False):
    """Persist executed q_window plus issued carry; never label q_next executed.

    executed_state_path is a separately source-verified data-only projection,
    not an assertion that EXACT_CARRY's predicted issued SOC is executed SOC.
    """
    output = Path(output).resolve(); config_path=Path(config_path)
    config=preflight_config(output, config_path, expected_config_sha256)
    need(config.get('cadence')=='quarter','explicit quarter checkpoint cadence required')
    need(type(window)is int and 0<=window<96, 'invalid executed quarter')
    need(type(retrospective)is bool, 'explicit retrospective mode required')
    need(retrospective or 'checkpoint_root'not in config,'alternate checkpoint_root only for read-only retrospective serialization')
    plan=read(output/'PLAN.json');attempt=plan['execution_attempt_id']
    need(attempt==config['execution_attempt_id'], 'PLAN/config attempt mismatch')
    if(output/'RESUME_PLAN.json').exists():
        need(read(output/'RESUME_PLAN.json')['execution_attempt_id']==attempt,'resume attempt mismatch')
    folder=output/'pipeline/dispatch'/('W'+str(window))
    for rel in ('RESULT.json','EXACT_CARRY.json','BEFORE_HANDOFF.json','prepared/CARRY.json',
                'prepared/SUMMARY.json','actual/ACCEPTED_PAIR.json'):
        need((folder/rel).is_file(),'missing quarter handoff: '+str(folder/rel))
    pair=read(folder/'actual/ACCEPTED_PAIR.json');result=read(folder/'RESULT.json')
    need(pair['status']=='accepted_research_pair'and pair.get('safety_release')is False,
         'quarter lacks accepted research pair')
    from .same_period import enabled
    same_period=enabled(plan);need(same_period,'provincial checkpoint rejects old grid');offset=0
    need(result['window']==window and result['executed_end_exclusive']==(window+1)and
         result['issued_end_exclusive']==(window+1+offset),'executed/issued clocks differ')
    carry=read(folder/'EXACT_CARRY.json');incoming=read(folder/'prepared/CARRY.json')
    need(carry['delivery_tick']==(window+1+offset)and carry['executed_end_exclusive']==(window+1),
         'issued carry clock mismatch')
    need(incoming['delivery_tick']==(window+offset),'incoming boundary does not end executed quarter')
    pinned(folder/'EXACT_CARRY.json',result['handoff']['carry_sha256'])
    need(Path(result['handoff']['carry_path']).resolve()==(folder/'EXACT_CARRY.json').resolve(),
         'carry identity differs')
    occurrences=set()
    for stage in ('SCUC','SCED'):
        d=folder/'actual'/stage
        for name in ('JOB.json','COMPLETED.json','request.json.gz','response.json.gz'):
            need((d/name).is_file(),'unknown native outcome: '+str(d))
        key=canonical(d);need(key not in occurrences,'duplicate native occurrence');occurrences.add(key)
        need(read(d/'COMPLETED.json')['solve_entries']==1,'native count not one')
        pinned(d/'response.json.gz',pair[stage+'_response_sha256'])
    price=output/'pipeline/price'/('q'+str(window));accepted=read(price/'ACCEPTED.json')
    need(accepted['context']['run_id']==result['branch_id']and accepted['context']['day_quarter']==window and accepted['KKT']['passed']is True,
         'executed quarter price not accepted')
    native=price/'prepared/native_once'
    for name in ('LAUNCH.json','IDENTITY.json','COMPLETED.json','request.json','response.json'):
        need((native/name).is_file(),'unknown executed-price native outcome')
    pinned(native/'response.json',read(native/'COMPLETED.json')['response_sha256'])
    snapshot=Path(executed_state_path).resolve();state=read(snapshot)
    # Exact schema validation is shared with the independent execution projection.
    validate_executed_projection(state, output, window, folder, incoming, carry)
    if same_period:expected_price_source=read(folder/'actual/SCED/COMPLETED.json')['response']['raw_sha256']
    elif window:expected_price_source=read(output/'pipeline/dispatch'/('W'+str(window-1))/'actual/SCED/COMPLETED.json')['response']['raw_sha256']
    else:expected_price_source=read(Path(plan['run'])/'seed/WARMSTART_SOURCE.json')['response_sha256']
    need(accepted['context']['SCED_response_sha256']==expected_price_source,'quarter price not bound to executed physical source')
    roots=[folder,price]
    if window: roots.append(output/'pipeline/dispatch'/('W'+str(window-1)))
    else: roots.append(Path(plan['run'])/'seed')
    extras=[snapshot,folder/'prepared/CARRY.json',folder/'EXACT_CARRY.json',folder/'RESULT.json',
            folder/'BEFORE_HANDOFF.json']
    next_price=output/'pipeline/price'/('q'+str(window+1))
    prefetched=(next_price/'prepared/native_once').exists()
    if window<95:
        need(retrospective or not prefetched,'checkpoint must precede next price native call')
        need(retrospective or not(output/'pipeline/dispatch'/('W'+str(window+1))/'actual').exists(),
             'checkpoint must precede next physical native call')
        if retrospective and prefetched: roots.append(next_price)
    # Keep already-produced hour arithmetic, but its absence at a quarter boundary
    # is not filled or represented as an accepted hour.
    if window%4==3:
        for stem in ('HOUR','EXECUTION_H','SETTLEMENT_H','FOUR_PRICE_ARITHMETIC_INPUTS_H'):
            f=output/'pipeline/price'/(stem+str(window//4)+'.json')
            if f.is_file():extras.append(f)
    for name in ('POST_D1_REFERENCE','D1_REFERENCE'):
        ref=output/'pipeline'/name
        if ref.exists():
            need((ref/'ACCEPTED_REFERENCE.json').exists(),'unknown reference native outcome')
            roots.append(ref)
    future_price=None
    if prefetched:
        n=next_price/'prepared/native_once'
        for f in ('LAUNCH.json','IDENTITY.json','COMPLETED.json','request.json','response.json'):need((n/f).is_file(),'unknown prefetched price outcome')
        done=read(n/'COMPLETED.json');pinned(n/'response.json',done['response_sha256'])
        future_price=dict(quarter=window+1,accepted_path=str(next_price/'ACCEPTED.json'),accepted_sha256=sha(next_price/'ACCEPTED.json'),request_path=str(n/'request.json'),request_sha256=sha(n/'request.json'),response_path=str(n/'response.json'),response_sha256=sha(n/'response.json'),counts_as_executed=False)
    fields=dict(hour=window//4,window=window,last_window=window,next_price_quarter=window+1,
                resume_contract=state.get('resume_contract'),already_solved_future_price=future_price,
                executed_cells=list(range(window,(window+1))),
                executed_end_exclusive=(window+1),issued_end_exclusive=(window+1+offset),rt_clock_mode=plan.get('rt_clock_mode','legacy_issued_prefix'),grid_minutes=15,
                executed_projection=str(snapshot),executed_projection_sha256=sha(snapshot),
                retrospective=retrospective,snapshot_operation='read_only_retrospective'if retrospective else'live_pre_next_native_barrier',prefetched_next_price_preserved=prefetched,
                prefetched_next_price_counted_as_executed=False,
                progress=dict(computed=True,validated=True,local_checkpoint=True,
                              external_saved=False,restore_verified=None))
    return _seal_selected(output,config_path,expected_config_sha256,config,roots,extras,'quarter',window,fields)


def pack(directory):
    directory = Path(directory); ready = directory / 'READY.json'; z = read(ready)
    label = ('_Q%02d' % z['window']) if z.get('checkpoint_kind')=='quarter' else ('_H%02d' % z['hour'])
    archive = directory / ('Guangdong_' + z['execution_attempt_id'] + label + '.tar.gz')
    need(not archive.exists(), 'archive already exists; inspect instead of overwriting')
    with tarfile.open(archive, 'w:gz', compresslevel=5) as tf:
        tf.add(ready, arcname='READY.json', recursive=False)
        for item in z['files']:
            pinned(item['source_path'], item['sha256'])
            tf.add(item['source_path'], arcname=item['member'], recursive=False)
    # Local payload verification only, not old-chain replay or cloud download.
    with tarfile.open(archive, 'r:gz') as tf:
        for item in z['files']:
            member = tf.getmember(item['member']); stream = tf.extractfile(member)
            need(member.isfile() and member.size == item['size'] and stream is not None,
                 'archive payload missing')
            h = hashlib.sha256()
            for block in iter(lambda: stream.read(1024 * 1024), b''): h.update(block)
            need(h.hexdigest() == item['sha256'], 'archive payload changed while packing')
    info = dict(schema='gd.durable.archive.v1', path=str(archive),
                execution_attempt_id=z['execution_attempt_id'], hour=z['hour'],
                checkpoint_kind=z.get('checkpoint_kind','hour'), window=z.get('window'),
                manifest_sha256=sha(ready), archive_sha256=sha(archive),
                archive_bytes=archive.stat().st_size, payload_files=len(z['files']))
    atomic(directory / 'ARCHIVE.json', info); return info

def acknowledge(directory, library_file_id, *, archive_sha256, archive_bytes, upload_result_path):
    """Only call after Library create/finalize actually succeeds for these bytes."""
    directory = Path(directory); a = read(directory / 'ARCHIVE.json')
    need(library_file_id.startswith('libfile_'), 'real Library file ID required')
    need(read(directory / 'READY.json')['policy'] == 'cloud_required', 'local policy cannot claim cloud acknowledgement')
    need(a['archive_sha256'] == archive_sha256 and a['archive_bytes'] == archive_bytes,
         'uploaded archive identity differs')
    pinned(a['path'], archive_sha256); pinned(directory / 'READY.json', a['manifest_sha256'])
    raw = Path(upload_result_path).read_bytes(); evidence = json.loads(raw)
    request = evidence['request']
    need(evidence.get('tool') == 'library.create_library_file' and
         Path(request['file']).resolve() == Path(a['path']).resolve() and
         request['sha256'] == archive_sha256 and request['bytes'] == archive_bytes,
         'upload request evidence does not bind exact local archive')
    def walk(obj):
        if isinstance(obj, dict):
            for k, v in obj.items():
                need(not (k == 'isError' and v is True), 'failed upload evidence')
                yield k, v; yield from walk(v)
        elif isinstance(obj, list):
            for v in obj: yield from walk(v)
    fields = list(walk(evidence['response']))
    need(any(k in ('library_file_id', 'id') and v == library_file_id for k, v in fields),
         'upload result does not contain confirmed Library identity')
    sizes = [v for k,v in fields if k in ('size_bytes','file_size_bytes') and type(v) is int]
    need(not sizes or archive_bytes in sizes, 'uploaded size differs')
    proof = directory / 'UPLOAD_TOOL_RESULT.json'
    with proof.open('xb') as f: f.write(raw); f.flush(); os.fsync(f.fileno())
    z = dict(schema='gd.durable.upload_receipt.v1', library_file_id=library_file_id,
             execution_attempt_id=a['execution_attempt_id'], hour=a['hour'],
             checkpoint_kind=a.get('checkpoint_kind','hour'), window=a.get('window'),
             archive_sha256=archive_sha256, archive_bytes=archive_bytes,
             manifest_sha256=a['manifest_sha256'],
             upload_result_sha256=sha(proof),
             meaning='coordinator-observed successful Library upload, not mathematical acceptance',
             native_calls=0, safety_release=False)
    atomic(directory / 'UPLOADED.json', z); return z

def validate_receipt(directory, *, local_archive=True, require_readback=None):
    directory = Path(directory); r = read(directory / 'UPLOADED.json'); a = read(directory / 'ARCHIVE.json')
    need(r['schema'] == 'gd.durable.upload_receipt.v1' and r['library_file_id'].startswith('libfile_'),
         'unsupported upload receipt')
    pinned(directory / 'READY.json', r['manifest_sha256'])
    z = read(directory / 'READY.json')
    need(z['policy'] == 'cloud_required', 'local package is not cloud persisted')
    pinned(directory / 'UPLOAD_TOOL_RESULT.json', r['upload_result_sha256'])
    need(r['execution_attempt_id'] == z['execution_attempt_id'] and r['hour'] == z['hour'],
         'receipt belongs to another attempt/hour')
    need(r.get('checkpoint_kind','hour')==z.get('checkpoint_kind','hour') and r.get('window')==z.get('window'),
         'receipt belongs to another quarter boundary')
    need(all(r[k] == a[k] for k in ('manifest_sha256', 'archive_sha256', 'archive_bytes',
                                  'execution_attempt_id', 'hour')),
         'upload receipt mismatch')
    if local_archive: pinned(a['path'], r['archive_sha256'])
    if require_readback is True or(require_readback is None and z.get('require_download_ack')):
        check_download_ack(directory,r)
    return r

def acknowledge_download(directory,downloaded_archive,*,library_file_id,materialization_evidence):
    """After a real Library materialization: stream-check every downloaded member.
    The evidence is the coordinator's sanitized tool receipt, not credentials.
    Local archive duplication is not an authorized call to this function.
    """
    d=Path(directory);r=validate_receipt(d,require_readback=False);p=Path(downloaded_archive).resolve()
    need(library_file_id==r['library_file_id'],'downloaded Library identity mismatch')
    original=Path(read(d/'ARCHIVE.json')['path']).resolve()
    need(p!=original and p.stat().st_ino!=original.stat().st_ino,'readback must use independently materialized bytes, not the original archive')
    evidence=read(materialization_evidence)
    need(evidence.get('tool')=='library.prepare_materialize' and evidence.get('library_file_id')==library_file_id
         and Path(evidence['materialized_path']).resolve()==p and evidence.get('tool_completed') is True,
         'actual successful Library materialization receipt required')
    need(p.stat().st_size==r['archive_bytes'],'downloaded archive size mismatch');pinned(p,r['archive_sha256'])
    with tarfile.open(p,'r:*') as tf:
        ready=tf.extractfile('READY.json').read();need(hashlib.sha256(ready).hexdigest()==r['manifest_sha256'],'downloaded checkpoint manifest mismatch')
        manifest=json.loads(ready);expected={'READY.json',*[v['member']for v in manifest['files']]}
        need(len(tf.getmembers())==len(expected) and {v.name for v in tf.getmembers()}==expected,'unexpected/duplicate archive members')
        for item in manifest['files']:
            member=tf.getmember(item['member']);need(member.isfile() and member.size==item['size'],'downloaded payload metadata mismatch')
            stream=tf.extractfile(member);h=hashlib.sha256()
            for block in iter(lambda:stream.read(1024*1024),b''):h.update(block)
            need(h.hexdigest()==item['sha256'],'downloaded payload checksum mismatch')
    atomic(d/'MATERIALIZATION_TOOL_RESULT.json',evidence)
    proof=dict(schema='gd.durable.download_verified.v1',library_file_id=library_file_id,
               archive_sha256=r['archive_sha256'],archive_bytes=r['archive_bytes'],manifest_sha256=r['manifest_sha256'],
               payload_files=len(manifest['files']),upload_receipt_sha256=sha(d/'UPLOADED.json'),
               materialization_receipt_sha256=sha(d/'MATERIALIZATION_TOOL_RESULT.json'),
               downloaded_path=str(p),meaning='independently materialized archive and all payload hashes verified; not continuation tested',
               restore_continuation_verified=False,native_calls=0)
    atomic(d/'DOWNLOAD_VERIFIED.json',proof);return proof


def check_download_ack(directory,upload):
    d=Path(directory);proof=read(d/'DOWNLOAD_VERIFIED.json')
    need(proof.get('schema')=='gd.durable.download_verified.v1','unsupported readback ACK')
    for key in ('library_file_id','archive_sha256','archive_bytes','manifest_sha256'):
        need(proof.get(key)==upload[key],'download ACK belongs to another archive')
    pinned(d/'UPLOADED.json',proof['upload_receipt_sha256'])
    pinned(d/'MATERIALIZATION_TOOL_RESULT.json',proof['materialization_receipt_sha256'])
    need(proof['payload_files']==len(read(d/'READY.json')['files']),'download ACK incomplete')
    return proof


def wait_for_upload(directory, timeout_s=1800, poll_s=1):
    directory = Path(directory); deadline = time.monotonic() + timeout_s
    need(read(directory / 'READY.json')['policy'] == 'cloud_required', 'cloud wait requires explicit policy')
    while True:
        if (directory / 'STOP_REQUESTED').exists(): raise RuntimeError('checkpoint paused by operator; no retry')
        if (directory / 'UPLOADED.json').exists():
            if not read(directory/'READY.json').get('require_download_ack') or(directory/'DOWNLOAD_VERIFIED.json').exists():
                return validate_receipt(directory)
        if time.monotonic() >= deadline: raise TimeoutError('checkpoint not persisted; next native call blocked')
        time.sleep(min(poll_s, max(0, deadline-time.monotonic())))

def begin_upload(directory):
    directory = Path(directory); a = read(directory / 'ARCHIVE.json')
    need(not (directory / 'UPLOADED.json').exists(), 'already persisted; no repeat upload')
    pinned(directory / 'READY.json', a['manifest_sha256']); pinned(a['path'], a['archive_sha256'])
    atomic(directory / 'UPLOAD_ATTEMPT.json', dict(a, action='one Library create, unknown outcome must be reconciled'))
    return a

def _restore_status(sidecar, *, continuation_output=None):
    if not Path(sidecar).is_file(): return None, None
    try:
        from gd_resume_runtime.restore_evidence import verify
        proof = verify(sidecar)
        if continuation_output is not None:
            need(Path(proof['continuation_output']) == Path(continuation_output), 'restore verification belongs to another continuation output')
        return proof, None
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return None, str(exc)

def status(output):
    output = Path(output); result = []
    candidates = list((output / 'DURABLE_HOURS').glob('H*/READY.json')) + list((output / 'DURABLE_QUARTERS').glob('Q*/READY.json'))
    for ready in sorted(candidates):
        z = read(ready); d = ready.parent
        state = 'ready_to_pack'
        if (d / 'ARCHIVE.json').exists(): state = 'ready_to_upload'
        if (d / 'UPLOAD_ATTEMPT.json').exists(): state = 'upload_outcome_unknown_do_not_retry'
        if (d / 'UPLOADED.json').exists():
            validate_receipt(d, local_archive=False,require_readback=False);state='uploaded_waiting_readback'
            if not z.get('require_download_ack') or(d/'DOWNLOAD_VERIFIED.json').exists():
                validate_receipt(d,local_archive=False);state='cloud_persisted'
        if z['policy'] == 'local_only': state = 'local_not_cloud_persisted'
        proof, proof_error = _restore_status(d / 'RESTORE_VERIFICATION.json')
        if proof and proof['checkpoint_manifest_sha256'] != sha(ready):
            proof, proof_error = None, 'restore verification belongs to another checkpoint'
        result.append(dict(directory=str(d), execution_attempt_id=z['execution_attempt_id'],
                           hour=z['hour'], window=z.get('window'), checkpoint_kind=z.get('checkpoint_kind','hour'),
                           computed=True, validated=True, local_checkpoint=True,
                           external_saved=state=='cloud_persisted', restore_verified=proof, restore_verification_error=proof_error,
                           manifest_sha256=sha(ready), state=state))
    return dict(checkpoints=result, native_calls=0, safety_release=False)

def progress(output):
    """Reliable progress is separate from local/accepted progress; no fake restore claim."""
    records=[r for r in status(output)['checkpoints']if r['checkpoint_kind']=='quarter']
    source_proof, source_error = _restore_status(Path(output) / 'RESTORE_VERIFICATION.json', continuation_output=output)
    latest=lambda pred:max((r['window']for r in records if pred(r)),default=None)
    return dict(computed=latest(lambda r:r['computed']), validated=latest(lambda r:r['validated']),
                local_checkpoint=latest(lambda r:r['local_checkpoint']),
                external_saved=latest(lambda r:r['external_saved']),restore_verified=latest(lambda r:r['restore_verified'] is not None),
                source_checkpoint_restore_verified=source_proof, restore_verification_error=source_error,
                unit='zero_based_executed_15min_window',native_calls=0,safety_release=False)

if __name__ == '__main__':
    p = argparse.ArgumentParser(); p.add_argument('command', choices=['status', 'pack', 'begin-upload', 'ack']); p.add_argument('directory')
    p.add_argument('--library-file-id'); p.add_argument('--archive-sha256'); p.add_argument('--archive-bytes', type=int)
    p.add_argument('--upload-result-path')
    a = p.parse_args()
    if a.command == 'status': z = status(a.directory)
    elif a.command == 'pack': z = pack(a.directory)
    elif a.command == 'begin-upload': z = begin_upload(a.directory)
    else: z = acknowledge(a.directory, a.library_file_id, archive_sha256=a.archive_sha256,
                          archive_bytes=a.archive_bytes, upload_result_path=a.upload_result_path)
    print(json.dumps(z, ensure_ascii=False))
