"""Read-only archive import; no solver launch, no evidence rewriting.
Materialize exact bytes as content-addressed files with an explicit lexical map.
Only COMPLETE contiguous verified quarter prefix is accepted.
"""
from pathlib import Path
import hashlib,json,tarfile

def sha(b):return hashlib.sha256(b).hexdigest()
def need(v,m):
 if not v:raise ValueError(m)
def import_prefix(receipts,destination):
 destination=Path(destination);need(not destination.exists(),'destination already exists')
 staged={};archives=[];origin=None
 for expected,receipt in enumerate(receipts):
  ack=json.loads(Path(receipt).read_bytes());raw=Path(ack['downloaded_path']).read_bytes()
  need(sha(raw)==ack['archive_sha256'],'archive SHA mismatch')
  with tarfile.open(ack['downloaded_path'])as t:
   names=t.getnames();need(len(names)==len(set(names)),'duplicate members')
   manifest=t.extractfile('READY.json').read();need(sha(manifest)==ack['manifest_sha256'],'manifest SHA mismatch');r=json.loads(manifest)
   need(r['window']==expected and r['executed_end_exclusive']==expected+1,'noncontiguous executed boundary')
   need(r['rt_clock_mode']=='provincial_15min_execution'and r['grid_minutes']==15,'wrong clock')
   need(origin is None or origin==r['output'],'mixed logical branches');origin=r['output']
   need(len(r['files'])==ack['payload_files'],'payload count mismatch')
   expected_members={'READY.json'}|{f['member']for f in r['files']};need(set(names)==expected_members,'unexpected archive members')
   for f in r['files']:
    member=t.getmember(f['member']);need(member.isfile(),'nonregular payload')
    data=t.extractfile(member).read();need(len(data)==f['size'] and sha(data)==f['sha256'],'payload mismatch')
    old=f['restore_path'];need(Path(old).is_absolute(),'nonabsolute source identity')
    staged[(old,f['sha256'])]=data
   archives.append({'window':expected,'library_file_id':ack['library_file_id'],'archive_sha256':ack['archive_sha256'],'manifest_sha256':ack['manifest_sha256']})
 need(bool(archives),'empty prefix')
 destination.mkdir();(destination/'objects').mkdir();mapping={}
 for (old,version),data in staged.items():
  digest=sha(data);p=destination/'objects'/digest
  if not p.exists():p.write_bytes(data)
  mapping.setdefault(old,{})[version]={'path':str(p.resolve()),'sha256':digest,'bytes':len(data)}
 result={'schema':'gd.isolated_archive_import.v1','logical_original_output':origin,'first_unexecuted_window':len(archives),'map':mapping,'archives':archives,'old_bytes_changed':False,'source_closure_verified':False,'continuation_tested':False,'native_calls':0}
 (destination/'IMPORT.json').write_text(json.dumps(result,indent=2));return result

def resolve_verified(manifest,old,expected_sha):
 """No fallback to an original path. Every read verifies actual bytes."""
 z=json.loads(Path(manifest).read_bytes());d=z['map'].get(old,{}).get(expected_sha);need(d is not None,'unmapped source version; fail closed')
 need(d['sha256']==expected_sha,'unexpected historical identity');p=Path(d['path']);need(p.is_file() and not p.is_symlink(),'nonregular mapped file');b=p.read_bytes()
 need(len(b)==d['bytes']and sha(b)==expected_sha,'mapped payload changed');return b
if __name__=='__main__':
 import sys
 print(json.dumps(import_prefix(sys.argv[2:],sys.argv[1]),indent=2))
