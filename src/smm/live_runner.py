"""Atomic incremental run evidence; no Streamlit dependency or market re-solving."""
from __future__ import annotations
import json
import os
import shutil
from pathlib import Path

class RunCancelled(RuntimeError):
    """User requested a stop at an optimization boundary; no final settlement."""


def atomic_json(path, value):
    path = Path(path)
    tmp = path.with_name(path.name + f'.{os.getpid()}.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, default=str, allow_nan=False), encoding='utf-8')
    tmp.replace(path)


def read_live(directory):
    path = Path(directory) / 'live.json'
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}


def freeze_inputs(root, directory, bids=None, regulation=None):
    """Snapshot before launching: later scenario edits cannot change an active run."""
    from .unit_edit_transaction import scene_input_lock
    with scene_input_lock(root):
        return _freeze_inputs_unlocked(root, directory, bids, regulation)


def _freeze_inputs_unlocked(root, directory, bids=None, regulation=None):
    root, directory = Path(root), Path(directory)
    from .config import load_config
    from .regulation import validate_config
    import copy
    preflight=load_config(root=root)
    if regulation is not None:preflight.mode['regulation']=copy.deepcopy(regulation)
    validate_config(preflight)  # before spawning or modifying the frozen input tree
    snap = directory / 'scenario_snapshot'
    if snap.exists():
        raise FileExistsError('运行输入快照已存在，禁止覆盖')
    for folder in ('config', 'data', 'bids'):
        if (root/folder).exists(): shutil.copytree(root/folder, snap/folder)
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
    capture_provenance(directory, snap, filename="frozen_input_provenance.json")
    return snap


class ProgressWriter:
    def __init__(self, directory, total=96):
        self.directory=Path(directory);self.total=total;self.da=None
        self.state={'phase':'preparing','executed_periods':0,'total_periods':total,'expected_periods':total,'settled':False, 'settlement_calculated':False,'settlement_scope':'not_applicable','completed_scope':None}
        self.write()
    def cancelled(self): return (self.directory/'cancel.request').exists()
    def check_cancel(self):
        if self.cancelled(): raise RunCancelled('用户取消：保留已执行前缀，不生成完整日结算')
    def write(self): atomic_json(self.directory/'live.json',self.state)
    def __call__(self, event):
        phase=event['phase'];self.state['phase']=phase
        if phase=='da_stage':
            self.state['da_stages']=event.get('stage_diagnostics',{})
        elif phase=='da_done':
            self.da=event['da']
            self.state['da_stages']=getattr(self.da,'stage_diagnostics',{})
            self.state['da_prices']=json.loads(self.da.tables['prices'].to_json(orient='records'))
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


def completion_metadata(expected, executed, *, mode='full', calculated=True):
    expected,executed=int(expected),int(executed)
    complete=mode=='full' and executed==expected
    return dict(completed_scope='da_only' if mode=='da' else ('full_day' if complete else 'debug_prefix'),
                expected_periods=expected,executed_periods=executed,
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


def capture_provenance(directory, input_root, *, filename='run_provenance.json', source_root=None):
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
    atomic_json(Path(directory)/filename,evidence)
    return evidence
