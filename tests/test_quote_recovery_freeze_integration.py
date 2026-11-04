"""A recovered unit's quote, not its old saved version, reaches frozen inputs."""
import copy
import json

import pytest

from test_input_recovery_ui import scenario, item, open_app, files
from test_quote_backup_recovery import supply_upload


@pytest.mark.parametrize('uid,curve', [
    ('C1', 'segments'), ('CCGT', 'segments'), ('GT', 'segments'),
    ('W1', 'segments'), ('PV1', 'segments'), ('H1', 'segments'),
    ('ES1', 'dis_segments'), ('PS1', 'gen_segments'),
])
def test_recovered_quote_is_exactly_frozen_for_all_resource_types(scenario, monkeypatch, uid, curve):
    from smm.bids import load_bids
    from smm.config import load_config
    from smm.live_runner import freeze_inputs

    source = scenario.scenario_path('默认')
    original_files = files(source)
    at = open_app()
    item(at.selectbox, '模拟机组').select(uid).run()
    current = 'quote_current:默认'
    original = copy.deepcopy(at.session_state[current])
    backup = copy.deepcopy(original)
    for row in backup['units'][uid][curve]:
        row['price'] += 10.
    supply_upload(monkeypatch, backup)
    at.run()
    assert at.session_state[current] == original
    item(at.button, '恢复文件中本机组报价').click().run()
    assert not at.exception
    assert not item(at.button, '▶ 开始运行').disabled
    expected = copy.deepcopy(original)
    expected['units'][uid] = backup['units'][uid]
    assert at.session_state[current] == expected
    durable = (source / 'current_quote.json').read_bytes()
    assert json.loads(durable)['draft'] == expected
    directory = scenario.RUNS_DIR / f'recovered_{uid}'
    frozen = freeze_inputs(source, directory, bids=expected)
    assert json.loads((directory / 'submitted_bids.json').read_bytes()) == expected
    assert load_bids(load_config(root=frozen))['units'] == expected['units']
    assert (source / 'current_quote.json').read_bytes() == durable
    assert files(source) == original_files
    for name in ('base.yaml', 'modes.yaml', 'units.yaml'):
        assert (frozen / 'config' / name).read_bytes() == (source / 'config' / name).read_bytes()
