from pathlib import Path
import sys,json,math,hashlib
from copy import deepcopy
from datetime import datetime,timedelta
B=Path(__file__).resolve().parent;R=B.parent
sys.path[:0]=[str(R/'guangdong_r62_same_source_da_rt_20261112'),str(R/'guangdong_r46_curtailable_full_day_20261112'),str(R/'guangdong_after_reset_r23_compressed_20261112/repo/src'),str(R/'guangdong_r53_same_source_da_chain_20261112')]
from r62smm import gd_r46_source_contract as source,gd_r46_day_session as session,gd_r46_day_models as local_models,highs_solver as hs
from r62smm.gd_r15_da_rt_bridge import require,close,digest,canonical,file_sha,integrate_cell
from r62smm.gd_r15_two_window import project_issued_prefix
from r62smm.gd_october_research_forecast import prior_value
OPTIONS=dict(threads=1,time_limit=120.,mip_rel_gap=.001,mip_abs_gap=0.,output_flag=False,presolve='choose',solve_relaxation=False)
CONFIG=R/'guangdong_r62_same_source_da_rt_20261112/SOURCE_CONFIG.json'
read=lambda p:json.loads(Path(p).read_bytes())
def write(p,x):
 with Path(p).open('xb') as f:f.write(canonical(x))
def name(s):return s.replace("'",'')
