"""Bounded research batch hook. Host authenticates receipt and consumer source digests."""
from pathlib import Path
import sys
from hourly_day import digest,stamp,project,validate

def apply_available(receipts,consumer,base_agc,*,expected_receipt_digests,expected_consumer_digest,ledger=()):
 if digest(consumer)!=expected_consumer_digest:raise ValueError('consumer source identity')
 if len(receipts)>24:raise ValueError('bounded to24 receipts')
 if [digest(r)for r in receipts]!=expected_receipt_digests:raise ValueError('receipt source identity')
 seen=set();out=base_agc;applied=list(ledger);selected=[]
 for r in receipts:
  validate(r);c=r['context'];hour=c['hour_start']
  if hour in seen:raise ValueError('duplicate hour')
  seen.add(hour)
  if stamp(c['as_of'])>stamp(consumer['cutoff']):continue
  from datetime import timedelta
  begin=stamp(consumer['domain_start']);end=begin+timedelta(minutes=5*consumer['cells'])
  if stamp(c['hour_end'])<=begin or stamp(hour)>=end:continue
  z=project(r,consumer,out,already_applied=applied);out=z['agc'];applied=z['applied_receipts'];selected.append(hour)
 return dict(agc=out,applied_receipts=applied,selected_hours=selected,native_calls=0,formal=False,safety_release=False,already_consumed_by_existing_SCED=False,requires_new_SCED_for_changed_inputs=True)

if __name__=='__main__':
 import argparse,json
 p=argparse.ArgumentParser();p.add_argument('input');p.add_argument('output');a=p.parse_args()
 z=apply_available(**json.loads(Path(a.input).read_text()))
 with Path(a.output).open('x')as f:json.dump(z,f,indent=2,allow_nan=False)
