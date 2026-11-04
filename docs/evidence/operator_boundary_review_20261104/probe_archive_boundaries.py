"""Reproduce UI boundaries using existing archives; write only a temp copy and report.

Run from the checkout being tested with its app/src on PYTHONPATH. This does not
run an optimizer. Missing rows/files are controlled corruption, not market cases.
"""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import tempfile

from streamlit.testing.v1 import AppTest
import common
from result_navigation import validate_display_result
from smm import scenario

parser = argparse.ArgumentParser()
parser.add_argument('--archive', required=True, type=Path)
parser.add_argument('--report', required=True, type=Path)
args = parser.parse_args()
source = args.archive.resolve()
root = Path.cwd()
temp = Path(tempfile.mkdtemp(prefix='ieee39-archive-boundaries-'))
scenario.SCEN_DIR = temp / 'scenarios'; scenario.RUNS_DIR = temp / 'runs'
scenario.ensure_default()
get = common.get_result
original = get(dict(dir=str(source), mode='full'))
watched = [p for p in source.rglob('*') if p.is_file()]
before = {str(p.relative_to(source)): hashlib.sha256(p.read_bytes()).hexdigest() for p in watched}
reports = []
for damage in ('missing_period', 'missing_node', 'float_period', 'missing_rt_prices'):
    result = deepcopy(original)
    if damage == 'missing_period':
        result['rt_prices'] = result['rt_prices'].query('period != 40').copy()
    elif damage == 'missing_node':
        result['rt_prices'] = result['rt_prices'].query('bus != 16').copy()
    elif damage == 'float_period':
        for key in ('da_nodal_prices', 'da_unit_awards', 'rt_prices', 'rt_units', 'rt_system'):
            result[key]['period'] = result[key].period.astype(float)
    else:
        copy = temp / damage; copy.mkdir()
        for path in source.glob('*'):
            if path.is_file() and path.suffix in ('.csv', '.json') and path.name != 'rt_prices.csv':
                shutil.copy2(path, copy / path.name)
        result = get(dict(dir=str(copy), mode='full'))
    try:
        validate_display_result(result)
        validation = 'accepted'
    except Exception as exc:
        validation = str(exc)
    common.all_results = lambda: [dict(dir=str(source), mode='full', scenario=damage, status='done')]
    common.get_result = lambda meta, data=result: deepcopy(data)
    app = AppTest.from_file(str(root / 'app/主页.py'), default_timeout=90).run()
    app.switch_page('pages/3_结果.py').run()
    reports.append(dict(case=damage, full=result['full'], validation=validation,
        exceptions=[x.message for x in app.exception], errors=[x.value for x in app.error],
        execution_cards=[x.value for x in app.metric if x.label == '实时执行范围']))
after = {str(p.relative_to(source)): hashlib.sha256(p.read_bytes()).hexdigest() for p in watched}
assert before == after, 'Original archive changed'
report = dict(scope='Actual saved archive with controlled corruption; no optimization, browser or OS test.',
              archive=str(source), files_hashed=len(before), unchanged=True, cases=reports)
args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(report, ensure_ascii=False, indent=2))
