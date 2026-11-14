"""Process-local SHA256 reuse for ordinary filesystem changes, not kernel attacks.
No persisted cache, no mathematical verdict caching; first use reads all bytes.
"""
from pathlib import Path
import hashlib,os,stat
_CACHE={}
STATS={'hits':0,'full_reads':0,'bytes_read':0}
def signature(s):return (s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
def sha(path):
 p=Path(path).resolve(strict=True);before=p.stat();key=(str(p),*signature(before))
 if not stat.S_ISREG(before.st_mode):raise ValueError('regular source file required')
 old=_CACHE.get(str(p))
 if old is not None and old[0]==key:
  STATS['hits']+=1;return old[1]
 # One entry per resolved path; outdated versions cannot regain validity.
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
 result=h.hexdigest();_CACHE[str(p)]=(key,result)
 STATS['full_reads']+=1;STATS['bytes_read']+=count
 return result
