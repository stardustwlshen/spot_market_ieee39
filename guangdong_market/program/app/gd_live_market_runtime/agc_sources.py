"""Pin consumed AGC receipts alongside the accepted dispatch input, not guessed awards."""
from pathlib import Path
from datetime import datetime
from decimal import Decimal
import json,hashlib

def collect(prepared,expected_branch):
 prepared=Path(prepared).resolve();read=lambda p:json.loads(Path(p).read_bytes());sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
 binding=prepared.parent/'AGC_BINDING.json'
 if not binding.is_file():raise ValueError('accepted window lacks AGC binding receipt')
 award=read(prepared/'agc.json');summary=read(prepared/'SUMMARY.json');start=datetime.fromisoformat(summary['domain_start']);matched=[];pins={str(binding):sha(binding)}
 for bound in read(binding):
  if bound['run_id']!=expected_branch or datetime.fromisoformat(bound['domain_start'])!=start:raise ValueError('AGC binding branch/window mismatch')
  for filename,digest in bound['receipt_files'].items():
   receipt=Path(filename).resolve()
   if not receipt.is_file() or sha(receipt)!=digest:raise ValueError('AGC receipt changed before source acceptance')
   pins[str(receipt)]=digest;z=read(receipt);ctx=z['context'];result=z['result']
   if ctx['branch_id']!=expected_branch:raise ValueError('AGC receipt belongs to other branch')
   if datetime.fromisoformat(ctx['hour_start'])<=start<datetime.fromisoformat(ctx['hour_end']):
    if result['hour_start']!=ctx['hour_start'] or result['binding_end']!=ctx['hour_end']:raise ValueError('AGC receipt time mismatch')
    if not set(result['awards_mw'])<=set(award):raise ValueError('AGC executed inventory mismatch')
    for g in award:
     mw=result['awards_mw'].get(g,0)
     if award[g].get('qualification')=='no_award' and mw!=0:raise ValueError('unqualified resource received AGC')
     for side in ('up_mw','down_mw'):
      if Decimal(str(award[g][side]['0']))!=Decimal(str(mw)):raise ValueError('AGC receipt differs from executed first-period award')
    matched.append(str(receipt))
 if len(set(matched))!=1:raise ValueError('exactly one consumed first-period AGC receipt required')
 return pins
