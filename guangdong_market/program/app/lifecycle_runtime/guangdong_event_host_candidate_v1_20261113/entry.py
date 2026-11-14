"""Candidate integration CLI. No solver path exposed."""
from host import Session
import argparse,json
p=argparse.ArgumentParser();p.add_argument('--research-fixed-curve-events',action='store_true');p.add_argument('--output',required=True);a=p.parse_args()
s=Session(a.output,research_fixed_curve_events=a.research_fixed_curve_events);report=[]
for w in (45,46):
 built=s.prepare(w);hand=s.handoff(w);report.append(dict(window=w,rows=built['added_rows'],max_residual=built['added_residual'],handoff=hand))
print(json.dumps(dict(windows=report,new_native_calls=0,formal=False)))
