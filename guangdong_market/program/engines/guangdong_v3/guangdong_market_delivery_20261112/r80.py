"""Source-preserving R80 fixed-fixture adapter. Dryrun/import never solves."""
from pathlib import Path
import sys,json,argparse
HOME=Path(__file__).resolve().parent
sys.path.insert(0,str(HOME/'vendor/guangdong_r80_rolling_rt_scuc_20261112'))
import runner as r

def main():
 a=argparse.ArgumentParser();a.add_argument('action',choices=['prepare','run','fixture']);a.add_argument('--output',type=Path);a.add_argument('--execute-native',action='store_true');v=a.parse_args()
 if v.action=='fixture':print(json.dumps(r.fixture(),indent=2));return 0
 if v.output is None:a.error('--output required')
 out=v.output.resolve()
 if out.exists():raise ValueError('fresh output required')
 if v.action=='prepare':
  if v.execute_native:a.error('prepare does not execute native')
  m=r.build(0);q,n,_=r.serialize(m);r.preflight(q);r.save_prepared(out,q,n,dict(stage='RT_SCUC_W0',research=True,source='embedded R80 unchanged fixture',native_calls=0))
  print(json.dumps(dict(status='prepared_only',variables=len(q['variables']),rows=len(q['rows']),native_calls=0)));return 0
 if not v.execute_native:
  print(json.dumps(dict(status='dryrun_only',command=[sys.executable,'-B',str(Path(r.__file__).resolve()),'--output',str(out),'--execute-native'],max_native_calls=4,native_calls=0,fixture_only=True,prepared_W0_not_replayed=True)));return 0
 # Frozen runner builds its own causal two-window chain; never replays an old prepared permit.
 r.run(out);return 0
if __name__=='__main__':raise SystemExit(main())
