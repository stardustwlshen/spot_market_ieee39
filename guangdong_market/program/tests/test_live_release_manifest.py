"""Release gate: test the real launch source gate, not just direct Pipeline runs."""
from pathlib import Path
import hashlib
import json
import sys
import pytest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'app'))
import gd_live_control as control


def test_real_live_source_gate_and_new_dependencies():
    pins=control.sources()
    for name in ('gd_case_snapshot.py','gd_thermal_minimum.py'):
        assert str(ROOT/'app'/name) in pins
    import gd_unified_backend
    gd_unified_backend.trusted()


def test_live_and_application_manifests_agree():
    live=json.loads((ROOT/'app/LIVE_MARKET_MANIFEST.json').read_text())
    app=json.loads((ROOT/'APPLICATION_MANIFEST.json').read_text())['files']
    for name,h in live.items():
        raw=(ROOT/'app'/name).read_bytes()
        assert hashlib.sha256(raw).hexdigest()==h==app['app/'+name]['sha256']
    raw=(ROOT/'app/LIVE_MARKET_MANIFEST.json').read_bytes()
    assert hashlib.sha256(raw).hexdigest()==app['app/LIVE_MARKET_MANIFEST.json']['sha256']


def test_actual_source_gate_still_rejects_changed_bytes(tmp_path,monkeypatch):
    p=tmp_path/'module.py';p.write_bytes(b'value=1\n')
    (tmp_path/'LIVE_MARKET_MANIFEST.json').write_text(json.dumps({'module.py':hashlib.sha256(p.read_bytes()).hexdigest()}))
    monkeypatch.setattr(control,'B',tmp_path)
    control.sources()
    p.write_bytes(b'value=2\n')
    with pytest.raises(ValueError,match='live source changed module.py'):control.sources()
