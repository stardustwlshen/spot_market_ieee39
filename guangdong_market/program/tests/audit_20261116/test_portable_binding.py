"""Release checks must validate the nested runtime manifest, not only APP/LIVE."""
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import pytest
ROOT = Path(__file__).resolve().parents[2]

def load_gate(path):
    spec = importlib.util.spec_from_file_location('portable_binding_gate', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def test_release_binding_chain():
    for manifest, base, wrapped in [
        (ROOT/'APPLICATION_MANIFEST.json', ROOT, True),
        (ROOT/'app/LIVE_MARKET_MANIFEST.json', ROOT/'app', False),
        (ROOT/'app/lifecycle_runtime/PORTABLE_MANIFEST.json', ROOT/'app/lifecycle_runtime', True),
    ]:
        data = json.loads(manifest.read_bytes())
        for name, entry in (data['files'] if wrapped else data).items():
            expected = entry['sha256'] if isinstance(entry, dict) else entry
            assert hashlib.sha256((base/name).read_bytes()).hexdigest() == expected, name
    load_gate(ROOT/'app/lifecycle_runtime/portable_gate.py').verify()

@pytest.mark.parametrize('target', ['content_hash.py', 'PORTABLE_MANIFEST.json'])
def test_tampered_dependency_or_manifest_rejected(tmp_path, target):
    source = ROOT/'app/lifecycle_runtime'
    copy = tmp_path/'runtime'
    shutil.copytree(source, copy)
    path = copy/target
    path.write_bytes(path.read_bytes()+b'\n')
    with pytest.raises(ValueError, match='portable (dependency|manifest) drift'):
        load_gate(copy/'portable_gate.py').verify()
