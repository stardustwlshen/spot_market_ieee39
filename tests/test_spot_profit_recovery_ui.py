from streamlit.testing.v1 import AppTest
from test_spot_costs import save,record
from test_profit_recalculation import fixture_run,hashes
from test_profit_recovery_ui import open_app


def test_new_policy_button_persists_history_and_downloads(tmp_path,monkeypatch):
    from smm import config
    run,source=fixture_run(tmp_path)
    save(source,{'C1':record('coal',fuel_cost_yuan_per_mwh=8,online_cost_yuan_per_h=4,startup_cost_yuan_per_start=20),'ES1':record('storage')})
    monkeypatch.setattr(config,'ROOT',source)
    before=hashes(run);at=open_app([run]);assert not at.exception and not at.error
    next(x for x in at.button if x.label=='按现货口径重算本次利润').click().run()
    assert not at.exception and not at.error and hashes(run)==before
    assert next(x for x in at.metric if x.label=='补算利润').value=='138.0'
    new=open_app([run]);assert not new.exception and any('现货口径派生报告' in x.value for x in new.success)
    assert hashes(run)==before
