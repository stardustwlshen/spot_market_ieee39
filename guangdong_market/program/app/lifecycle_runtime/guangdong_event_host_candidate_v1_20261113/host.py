"""Portable base type; only fixed V6Session may authorize runtime operations."""
from pathlib import Path
import json,hashlib,sys
B=Path(__file__).resolve().parent
sys.path.insert(0,str(B.parent/'guangdong_fixed_event_entry_v1_20261113'))
import event_path as event
read=event.read
from content_hash import sha
def write(p,x):
 with Path(p).open('x')as f:json.dump(x,f,sort_keys=True,allow_nan=False)
class Session:
 def __init__(self,*a,**kw):raise ValueError('use registered portable V6Session; fixture registry unsupported')
