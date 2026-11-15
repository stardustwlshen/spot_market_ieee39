from pathlib import Path
import subprocess,sys
ROOT=Path(__file__).resolve().parents[1]
def test_single_day_terminal_checkpoint():
    subprocess.run([sys.executable,str(Path(__file__).with_name("single_day_terminal_fixture.py"))],check=True,capture_output=True,text=True)
def test_single_day_scope_no_nextday():
    code="""import sys
from pathlib import Path
A=Path(sys.argv[1]);sys.path[:0]=[str(A),str(A/'gd_live_market_runtime')]
import session
z=session.plan(windows=96,research_event_simulation=True)
assert z['maximum_native_calls']==288 and z['requires_explicit_nextday_selection']is False and z['single_day_scope']=='truncate_at_24h'
for kw in [dict(post_scuc_day=True),dict(reference_revision=True),dict(finite_hourly=True)]:
 try:session.plan(windows=96,**kw)
 except ValueError:pass
 else:raise AssertionError(kw)
"""
    subprocess.run([sys.executable,"-c",code,str(ROOT/'app')],check=True,capture_output=True,text=True)
