"""Fresh extracted source smoke, using an already verified installed environment."""
import sys,json,hashlib
from pathlib import Path
root=Path(sys.argv[1]).resolve()
sys.path[:0]=[str(root/'src'),str(root/'app')]
original={str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file()}
from smm.config import load_config
from smm.pipeline import prepare
from smm.bids import load_bids
from streamlit.testing.v1 import AppTest
cfg=load_config(root=root)
assert cfg.base['forecast']['model']=='multiscale_v2'
assert cfg.base['tie_line_mode']=='supply'
assert cfg.base['solver']['mip_rel_gap']==0.01
assert cfg.base['solver']['time_limit_s']==600
assert sum(r['cap'] for r in cfg.units['renewable'] if r['type']=='pv')==2152
assert load_bids(cfg)['version']==3
case=prepare(cfg,read_only=True,verbose=False)
assert original=={str(p):hashlib.sha256((root/p).read_bytes()).hexdigest() for p in original}
at=AppTest.from_file(str(root/'app/主页.py'),default_timeout=90).run()
assert not at.exception,[x.value for x in at.exception]
at.switch_page('pages/2_运行.py').run()
assert not at.exception,[x.value for x in at.exception]
assert any(b.label=='▶ 开始运行' for b in at.button)
assert original=={str(p):hashlib.sha256((root/p).read_bytes()).hexdigest() for p in original}
print(json.dumps({'source_directory':root.name,'forecast_model':'multiscale_v2','tie_mode':'supply','PV_mw':2152,'bids_version':3,'solver_gap':0.01,'solver_time_limit_s':600,'prepared_periods':cfg.T,'original_source_files_unchanged':True,'home_and_run_page_AppTest_exceptions':0,'new_environment_installation':False,'market_solve_performed':False,'pixel_browser_QA':False},ensure_ascii=False))
