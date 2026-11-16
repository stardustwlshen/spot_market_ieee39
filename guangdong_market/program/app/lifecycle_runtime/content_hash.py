"""Read actual source bytes at every verification boundary on all platforms.
Metadata checks detect ordinary concurrent replacement, not content identity.
_CACHE and hit counters remain import-compatible but never authorize a digest.
"""
from pathlib import Path
import hashlib,os,stat
_CACHE={}
STATS={'hits':0,'full_reads':0,'bytes_read':0}
def signature(s):return (s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
def sha(path):
 # Preserve OS traversal semantics (symlink/.. must not be normalized away).
 # stat/open/fstat/stat checks are race diagnostics; only byte reads compute SHA.
 p=Path(path).absolute();before=p.stat()
 if not stat.S_ISREG(before.st_mode):raise ValueError('regular source file required')
 # Windows ctime may be creation time; timestamp precision also varies by FS.
 # Never treat unchanged stat fields as evidence of unchanged source contents.
 _CACHE.pop(str(p),None)
 h=hashlib.sha256();count=0
 with p.open('rb') as f:
  opened=os.fstat(f.fileno())
  if signature(opened)!=signature(before):raise ValueError('source changed before hash read')
  while True:
   block=f.read(1024*1024)
   if not block:break
   h.update(block);count+=len(block)
  after_fd=os.fstat(f.fileno())
 after=p.stat()
 if signature(before)!=signature(after_fd) or signature(before)!=signature(after) or count!=before.st_size:
  raise ValueError('source changed during hash read')
 result=h.hexdigest()
 STATS['full_reads']+=1;STATS['bytes_read']+=count
 return result

