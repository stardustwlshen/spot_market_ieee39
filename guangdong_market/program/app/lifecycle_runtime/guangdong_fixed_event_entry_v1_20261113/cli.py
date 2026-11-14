"""Portable event entry; stdlib only, zero native."""
import argparse,json
from event_path import *
p=argparse.ArgumentParser();p.add_argument('action',choices=['export-plan','publish','build','carry']);p.add_argument('--bundle',required=True);p.add_argument('--expected-bundle-sha256',required=True);p.add_argument('--ledger');p.add_argument('--expected-ledger-sha256');p.add_argument('--cutoff-tick',type=int);p.add_argument('--simulate',action='store_true');p.add_argument('--output',required=True);a=p.parse_args();b=read(a.bundle);kw={'expected_bundle_sha256':a.expected_bundle_sha256}
if a.action=='export-plan':r=export_plan(b,**kw)
elif a.action=='publish':r=publish(b,read(a.ledger)if a.ledger else None,cutoff_tick=a.cutoff_tick,simulate=a.simulate,expected_previous_ledger_sha256=a.expected_ledger_sha256,**kw)
elif a.action=='build':r=build_candidate(b,read(a.ledger),expected_ledger_sha256=a.expected_ledger_sha256,**kw)
else:r=carry_candidate(b,read(a.ledger),expected_ledger_sha256=a.expected_ledger_sha256,**kw)
with open(a.output,'x')as f:json.dump(r,f,sort_keys=True,allow_nan=False)
