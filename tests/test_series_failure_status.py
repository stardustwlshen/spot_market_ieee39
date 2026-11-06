"""Failure-injection unit tests. Prior commit verifier is an explicit stub."""
import json
from copy import deepcopy
from pathlib import Path
import pytest
from smm import series,state_bridge as bridge
from multiday_fixture import micro_day

@pytest.mark.parametrize('phase',['freeze_inputs','verify_frozen_inputs','build_DA_case'])
def test_failure_after_existing_prefix_records_stage_and_preserves_dir(tmp_path,monkeypatch,phase):
    first,initial=micro_day(with_hydro=False)
    second,_=micro_day('2026-07-16',with_hydro=False)
    root=tmp_path/'run';prior=root/'day_2026-07-15';prior.mkdir(parents=True)
    (prior/'day_commit.json').write_text('{}')
    checkpoint=dict(checkpoint_hash='prior-verified',initial_state_hash=bridge.digest(bridge.pack_state(first.cfg,initial)),previous_checkpoint_hash=None)
    monkeypatch.setattr(series,'_verify_commit',lambda directory,fp:(checkpoint,{'date':'2026-07-15','rows':[]}))
    monkeypatch.setattr(bridge,'next_day_state',lambda cp,cfg,net:bridge.research_initial_state(cfg,acknowledge_constant_prehistory=True))
    if phase=='freeze_inputs':
        def fail(*a,**k):raise RuntimeError('injected freeze failure')
        monkeypatch.setattr(series,'_freeze',fail)
    elif phase=='verify_frozen_inputs':
        original=series.day_fingerprint
        monkeypatch.setattr(series,'day_fingerprint',lambda day:'mismatch' if day.cfg.root.name=='scenario_snapshot' else original(day))
    else:
        def fail(*a,**k):raise RuntimeError('injected case failure')
        monkeypatch.setattr('smm.case.build_da_case',fail)
    with pytest.raises((RuntimeError,ValueError)):
        series.run_series([first,second],root,initial_actual_state=initial)
    status=json.loads((root/'series_status.json').read_text())
    assert status['status']=='failed_uncommitted_day'
    assert status['date']=='2026-07-16' and status['completed_days']==1
    assert status['failure_phase']==phase
    assert (root/'day_2026-07-16').is_dir()
    assert not (root/'day_2026-07-16/day_commit.json').exists()
    with pytest.raises(series.UncommittedDayError):
        series.run_series([first,second],root,initial_actual_state=initial)
    assert (prior/'day_commit.json').read_text()=='{}'
