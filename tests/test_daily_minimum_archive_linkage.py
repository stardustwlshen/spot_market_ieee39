"""Real saved model evidence must survive the standalone validator adapter.

The tiny two-bus fixture intentionally lacks a frozen IEEE39 network inventory;
we assert the new daily-minimum gate, not a fabricated whole-archive green light.
"""
import json
from pathlib import Path
import shutil
import pytest
import yaml
from smm.full import run_full,write_full_outputs
from test_hydro_daily_energy import case
from test_validate_runs_evidence import validator

@pytest.fixture(scope='module')
def real_archive(tmp_path_factory):
    out=tmp_path_factory.mktemp('daily_minimum_real_archive')
    c=case(T=12,minimum=40.)
    c.cfg.mode['intraday'].update(enabled=True,interval_h=1.)
    c.cfg.mode['realtime']['hydro']='value';c.cfg.mode['realtime']['storage']['mode']='value'
    c.cfg.units['hydro'][0]['seasons']['wet']['v_end']=10.
    root=out/'scenario_snapshot';(root/'config').mkdir(parents=True)
    for name,data in [('base',c.cfg.base),('units',c.cfg.units),('modes',{c.cfg.rule_set:c.cfg.mode})]:
        (root/f'config/{name}.yaml').write_text(yaml.safe_dump(data))
    result=run_full(c.cfg,c,log=lambda *a:None)
    (root/'resolved_bids.json').write_text(json.dumps(result['da'].case.bids))
    write_full_outputs(result,out,make_plots=False)
    return out


def test_real_saved_adapter_and_csv_independently_pass_daily_gate(real_archive):
    checks=validator.checks(real_archive)
    assert checks['水电日电量下限合规'],checks['说明']
    assert not checks['验收通过']  # This is a small model, without the archived network inventory.
    assert len(checks['水电日电量下限逐主体'])==1
    assert checks['水电日电量下限逐主体'][0]['executed_mwh']>=40.-.001


@pytest.mark.parametrize('damage',['missing_evidence','missing_effective_bids','missing_id_round','changed_actual_cumulative','changed_csv_power','false_summary'])
def test_saved_evidence_cannot_be_replaced_by_green_summary(real_archive,tmp_path,damage):
    d=tmp_path/'copy';shutil.copytree(real_archive,d)
    if damage=='missing_evidence':(d/'hydro_window_evidence.json').unlink()
    elif damage=='missing_effective_bids':(d/'scenario_snapshot/resolved_bids.json').unlink()
    elif damage=='missing_id_round':
        p=d/'hydro_window_evidence.json';a=json.loads(p.read_text());a['id_rounds'].pop();p.write_text(json.dumps(a))
    elif damage=='changed_actual_cumulative':
        p=d/'rolling_actual_states.json';a=json.loads(p.read_text());a['windows'][1]['hydro_used']['H1']+=1.;p.write_text(json.dumps(a))
    elif damage=='changed_csv_power':
        import pandas as pd
        p=d/'rt_hydro.csv';a=pd.read_csv(p);a['P_mw']=0.;a.to_csv(p,index=False)
    else:
        p=d/'summary.json';a=json.loads(p.read_text());a['hydro_daily_minimum']['full_day']=False;p.write_text(json.dumps(a))
    checks=validator.checks(d)
    assert not checks['水电日电量下限合规'] and not checks['验收通过']
