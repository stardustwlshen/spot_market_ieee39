"""Offline integrity mutations use one module-scoped real solved archive."""
from pathlib import Path
import copy,json,shutil
import importlib.util
import pytest
from smm import result_retention as retention
from smm.state_bridge import digest
ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('retention_offline_validator', ROOT / 'scripts/validate_runs.py')
validator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(validator)
checks = validator.checks

@pytest.fixture(scope='module')
def solved_source(tmp_path_factory):
    from test_retention_real_evidence import test_same_solve_full_and_compact_archive_exact
    root=tmp_path_factory.mktemp('offline_same_solve')
    test_same_solve_full_and_compact_archive_exact(root)
    shutil.copytree(root/'frozen_inputs/scenario_snapshot',root/'compact/scenario_snapshot')
    return root/'compact'


@pytest.fixture
def archive(tmp_path,solved_source):
    d=tmp_path/'copy';shutil.copytree(solved_source,d)
    summary=json.loads((d/'summary.json').read_text())
    return d,summary


def test_official_offline_audit_accepts_complete_cache(archive):
    d,s=archive;assert retention.audit_archive(d,s)['valid']
    got=checks(d);assert got['ID数值缓存已验证'] is True


@pytest.mark.parametrize('damage',['missing','round','hash','foreign_resource','unexplained_resource','strict_bundle','clock','bool_schema','split_cache_value'])
def test_official_offline_audit_rejects_incomplete_cache(archive,damage):
    d,s=archive;p=d/'id_state_value_evidence.json';data=json.loads(p.read_text())
    if damage=='missing':p.unlink()
    else:
        row=data['rounds'][0];ev=row['evidence']
        if damage=='round':data['rounds'].pop()
        elif damage=='hash':ev['evidence_hash']='bad'
        elif damage=='clock':ev['window_start']+=1
        elif damage=='bool_schema':data['schema_version']=True
        elif damage=='strict_bundle':
            h=d/'hydro_window_evidence.json';v=json.loads(h.read_text());v['id_rounds'][0]['state_values']=None;h.write_text(json.dumps(v))
        else:
            if damage=='foreign_resource':ev['values']['E']['foreign']=copy.deepcopy(ev['values']['E']['ES1'])
            elif damage=='unexplained_resource':ev['values']['E'].pop('ES1')
            elif damage=='split_cache_value':ev['values']['V']['H1']['value_native'][0]=123.
            ev['evidence_hash']=digest({k:v for k,v in ev.items() if k!='evidence_hash'})
        p.write_text(json.dumps(data))
    with pytest.raises((ValueError,OSError)):retention.audit_archive(d,s)
    got=checks(d)
    assert got['ID数值缓存已验证'] is False and not got['证据完整'] and not got['验收通过']
    assert 'ID数值缓存未验证' in got['说明']


def test_legacy_without_marker_or_sidecar_is_not_newly_certified(archive):
    d,s=archive;s.pop('id_result_retention');(d/'id_state_value_evidence.json').unlink()
    assert retention.audit_archive(d,s)==dict(active=False,valid=None,scope='legacy_archive_without_numeric_ID_contract')
