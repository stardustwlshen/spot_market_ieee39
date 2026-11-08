"""Atomic incremental run evidence; no Streamlit dependency or market re-solving."""
from __future__ import annotations
import json
import os
import shutil
import time
from pathlib import Path

class RunCancelled(RuntimeError):
    """User requested a stop at an optimization boundary; no final settlement."""


def atomic_json(path, value):
    path = Path(path)
    tmp = path.with_name(path.name + f'.{os.getpid()}.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, default=str, allow_nan=False), encoding='utf-8')
    try:
        # Windows readers/AV may briefly deny replacement while holding a handle.
        # Retry only permission/sharing failures; critical callers still fail closed.
        for attempt in range(8):
            try:
                tmp.replace(path)
                break
            except PermissionError:
                if attempt == 7:
                    raise
                time.sleep(min(.01 * 2**attempt, .2))
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass  # Do not mask the write failure; only our exact temp path is touched.


def read_live(directory):
    path = Path(directory) / 'live.json'
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}


def freeze_inputs(root, directory, bids=None, regulation=None, *, run_options=None, bid_version=None):
    """Snapshot before launching: later scenario edits cannot change an active run."""
    from .unit_edit_transaction import scene_input_lock
    with scene_input_lock(root):
        return _freeze_inputs_unlocked(root, directory, bids, regulation, run_options=run_options, bid_version=bid_version)


def _freeze_inputs_unlocked(root, directory, bids=None, regulation=None, *, run_options=None, bid_version=None):
    root, directory = Path(root), Path(directory)
    from .config import load_config
    from .regulation import validate_config
    import copy
    preflight=load_config(root=root)
    from .spot_costs import load_profile as load_spot_costs
    load_spot_costs(preflight)  # malformed accounting inputs fail before launching; missing stays unknown
    if regulation is not None:preflight.mode['regulation']=copy.deepcopy(regulation)
    validate_config(preflight)  # before spawning or modifying the frozen input tree
    from .input_version import source_version
    source = source_version(root, bids=bids, bid_version=bid_version, options=run_options,
                            quote_policy='workspace' if bids is not None else 'archived')
    snap = directory / 'scenario_snapshot'
    if snap.exists():
        raise FileExistsError('运行输入快照已存在，禁止覆盖')
    for folder in ('config', 'data', 'bids'):
        if (root/folder).exists(): shutil.copytree(root/folder, snap/folder)
    from .reserve_policy import freeze_new_run_policy
    freeze_new_run_policy(snap, directory)
    if regulation is not None:
        import yaml
        from .config import load_config
        cfg=load_config(root=snap)
        mp=snap/'config/modes.yaml'
        modes=yaml.safe_load(mp.read_text())
        modes[cfg.rule_set]['regulation']=regulation
        mp.write_text(yaml.safe_dump(modes,allow_unicode=True,sort_keys=False))
    if bids is not None:
        from .config import load_config
        from .bids import validate_bids, raise_on_errors, bid_dir
        cfg = load_config(root=snap)
        raise_on_errors(validate_bids(cfg, bids, require_complete=True))
        dest = bid_dir(cfg);dest.mkdir(parents=True, exist_ok=True)
        atomic_json(dest/f"bids_v{bids['version']}.json", bids)
        atomic_json(directory/'submitted_bids.json', bids)
    from .reserve_policy import finalize_frozen_policy
    finalize_frozen_policy(snap, directory)
    capture_provenance(directory, snap, filename="frozen_input_provenance.json", source_version=source)
    return snap


class ProgressWriter:
    def __init__(self, directory, total=96):
        self.directory=Path(directory);self.total=total;self.da=None
        self.state={'phase':'preparing','executed_periods':0,'total_periods':total,'expected_periods':total,'settled':False, 'settlement_calculated':False,'settlement_scope':'not_applicable','completed_scope':None}
        self.write()
    def cancelled(self): return (self.directory/'cancel.request').exists()
    def check_cancel(self):
        if self.cancelled(): raise RunCancelled('用户取消：保留已执行前缀，不生成完整日结算')
    def activity(self, event):
        """Small boundary record; never serialize growing result tables on solver starts."""
        from datetime import datetime, timezone
        record = {key: event[key] for key in ('phase', 'activity', 'stage', 'period', 'total', 'completed') if key in event}
        record['updated_at'] = datetime.now(timezone.utc).isoformat()
        record.update(executed_periods=self.state['executed_periods'], total_periods=self.state['total_periods'])
        self._write_telemetry(self.directory/'progress.json', record)
    def _write_telemetry(self, path, value):
        try:
            atomic_json(path, value)
        except PermissionError as exc:
            # Live UI evidence is not settlement or business-state persistence.
            # Keep computing after bounded retries; never catch cancellation,
            # serialization/model errors, or critical meta/output writes here.
            last = getattr(self, '_write_warning_at', float('-inf'))
            if time.monotonic() - last >= 30.:
                print(f'[progress warning] Progress update temporarily blocked: {path.name}: {exc}', flush=True)
                self._write_warning_at = time.monotonic()
            blocked = getattr(self, '_telemetry_blocked_paths', set())
            blocked.add(path.name)
            self._telemetry_blocked_paths = blocked
        else:
            blocked = getattr(self, '_telemetry_blocked_paths', set())
            was_blocked = bool(blocked)
            blocked.discard(path.name)
            if was_blocked and not blocked:
                print('[progress recovered] Progress writing resumed.', flush=True)
    def write(self):
        from datetime import datetime, timezone
        self.state['updated_at'] = datetime.now(timezone.utc).isoformat()
        self._write_telemetry(self.directory/'live.json',self.state)
        self.activity(self.state)
    def __call__(self, event):
        phase=event['phase'];self.state['phase']=phase
        self.state['stage'] = event.get('stage')
        if phase=='da_stage':
            self.state['da_stages']=event.get('stage_diagnostics',{})
        elif phase=='da_done':
            self.da=event['da']
            self.state['da_stages']=getattr(self.da,'stage_diagnostics',{})
            self.state['da_prices']=json.loads(self.da.tables['prices'].to_json(orient='records'))
        elif phase=='id_round':
            rr=event['rr']
            self.state['id_stage_history']={str(r['tau']):r.get('stage_diagnostics',{}) for r in rr.id_rounds}
            self.state['id_rounds']=[dict(tau=r['tau'], issue_t=r.get('issue_t'),
                                         solution_quality=r.get('solution_quality',{})) for r in rr.id_rounds]
            self.state['id_failures']=list(getattr(rr,'id_failures',[]))
            self.state['id_failed_stage_history']={str(r['tau']):r.get('stages',{}) for r in getattr(rr,'id_failed_stages',[])}
            self.state['id_latest_tau']=event.get('tau')
            # ID progress is planning only. Never advance executed_periods or settled.
        elif phase=='rt_window':
            from .rolling import rt_tables
            da=event.get('da',self.da);rr=event['rr']
            tables=rt_tables(da,rr)
            self.state['executed_periods']=len(rr.rt)
            self.state['executed']={key:json.loads(tables[key].to_json(orient='records')) for key in ('units','prices','system','storage','pumped','hydro','lines','regulation','regulation_system') if key in tables}
            last=rr.rt[-1];sn=last['snap'];pr=last['prices'];t=last['t']
            look=[]
            for family in ('P','Pw','Ph','Pd','Pc','Pg','Pp'):
                for unit,vals in sn.get(family,{}).items():
                    for k,value in enumerate(vals):
                        look.append({'period':t+k,'unit':unit,'component':family,'power_mw':float(value),'status':'已执行' if k==0 else '前瞻计划（未执行）'})
            self.state['lookahead']=look
            self.state['window_start']=t
            self.state['hydro_window_history']={str(r['t']):r['hydro_window_targets'] for r in rr.rt if r.get('hydro_window_targets')}
            self.state['stage_history']={str(r['t']):r['stage_diagnostics'] for r in rr.rt if r.get('stage_diagnostics')}
        self.write()
        self.check_cancel()  # completed-stage boundary, never interrupt a solver mid-call


def completion_metadata(expected, executed, *, mode='full', calculated=True, dt_h=None):
    expected,executed=int(expected),int(executed)
    complete=mode=='full' and executed==expected and (dt_h is None or abs(expected * dt_h - 24.) < 1e-9)
    return dict(completed_scope='da_only' if mode=='da' else ('full_day' if complete else 'debug_prefix'),
                expected_periods=expected,executed_periods=executed,
                **({} if dt_h is None else dict(dt_h=float(dt_h), expected_hours=expected*dt_h, executed_hours=executed*dt_h)),
                settlement_scope='not_applicable' if mode=='da' or not calculated else ('full_day' if complete else 'illustrative_prefix'),
                settlement_calculated=bool(calculated and mode=='full'),settled=bool(complete and calculated))


def file_fingerprint(root, folders, *, suffix=None):
    """Bounded non-hidden regular files only; never follow symlinks or include environments."""
    import hashlib
    root=Path(root).resolve();entries={}
    for folder in folders:
        base=root/folder
        if not base.is_dir() or base.is_symlink():continue
        for path in sorted(base.rglob('*')):
            rel=path.relative_to(root)
            if any(part.startswith('.') or part in ('__pycache__','node_modules','venv') for part in rel.parts):continue
            if not path.is_file() or path.is_symlink() or any(p.is_symlink() for p in path.parents if p!=root and root in p.parents):continue
            if suffix is not None and path.suffix!=suffix:continue
            entries[rel.as_posix()]=hashlib.sha256(path.read_bytes()).hexdigest()
    canonical=json.dumps(entries,sort_keys=True,separators=(',',':')).encode()
    return dict(algorithm='sha256',files=entries,content_fingerprint=hashlib.sha256(canonical).hexdigest())


def capture_provenance(directory, input_root, *, filename='run_provenance.json', source_root=None, source_version=None):
    """Capture at invocation, not when results are later inspected; no secret/dotfile copies."""
    import subprocess
    if (Path(directory)/filename).exists():
        raise FileExistsError("运行来源记录已存在，禁止用后来源码覆盖")
    from datetime import datetime, timezone
    source_root=Path(source_root) if source_root is not None else Path(__file__).resolve().parents[2]
    def git(*args):
        try:return subprocess.check_output(['git','-C',str(source_root),*args],stderr=subprocess.DEVNULL,text=True).strip()
        except (OSError,subprocess.CalledProcessError):return None
    head=git('rev-parse','HEAD');status=git('status','--porcelain','--untracked-files=normal')
    evidence=dict(captured_at_utc=datetime.now(timezone.utc).isoformat(),
                  git_head=head,git_dirty=None if status is None else bool(status),
                  source_scope='src/app/scripts Python files only; excludes dotfiles, symlinks and environments',
                  source=file_fingerprint(source_root,('src','app','scripts'),suffix='.py'),
                  inputs=file_fingerprint(input_root,('config','data','bids')),
                  interpretation='content observed at run invocation; not a copied or sandboxed source tree')
    if source_version is not None:
        evidence['source_version'] = source_version
    atomic_json(Path(directory)/filename,evidence)
    return evidence
