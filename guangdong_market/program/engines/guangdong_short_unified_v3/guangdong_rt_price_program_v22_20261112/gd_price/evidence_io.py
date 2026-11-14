"""Version2 explicit gzip evidence refs. Both stored and original bytes are authenticated."""
import json,gzip,hashlib,os,zlib
from pathlib import Path
MAX_RAW=512*1024*1024
sha=lambda b:hashlib.sha256(b).hexdigest()
def store_bytes(path,raw):
 p=Path(path).resolve();p.parent.mkdir(parents=True,exist_ok=True)
 if len(raw)>MAX_RAW:raise ValueError('raw size limit')
 data=gzip.compress(raw,compresslevel=6,mtime=0)
 with p.open('xb')as f:f.write(data);f.flush();os.fsync(f.fileno())
 ref=dict(schema='gd.evidence.gzip.v2',path=str(p),codec='gzip',stored_sha256=sha(data),raw_sha256=sha(raw),raw_bytes=len(raw),stored_bytes=len(data))
 if read_bytes(ref)!=raw:raise ValueError('roundtrip')
 return ref
def read_bytes(ref):
 if ref['schema']!='gd.evidence.gzip.v2' or ref['codec']!='gzip':raise ValueError('codec')
 n=ref['raw_bytes']
 if type(n)is not int or not 0<=n<=MAX_RAW:raise ValueError('raw size')
 data=Path(ref['path']).read_bytes()
 if len(data)!=ref['stored_bytes']or sha(data)!=ref['stored_sha256']:raise ValueError('stored identity')
 d=zlib.decompressobj(31);raw=d.decompress(data,n+1)
 if len(raw)!=n or not d.eof or d.unused_data or d.unconsumed_tail or sha(raw)!=ref['raw_sha256']:raise ValueError('raw identity/multiple members')
 return raw
def save(path,obj):return store_bytes(path,json.dumps(obj,sort_keys=True,allow_nan=False,separators=(',',':')).encode())
def load(ref):return json.loads(read_bytes(ref))
def write_json(p,z):
 with Path(p).open('x')as f:json.dump(z,f,sort_keys=True,allow_nan=False)
