"""Persistent one-occurrence supervisor; failure never authorizes a retry."""
from pathlib import Path
import sys,json
from gd_price_ui_backend import read,write,invoke
p=Path(sys.argv[1]);j=read(p/'JOB.json')
try:
 invoke(j['installation'],j['args'],p/'cli.log',timeout=125)
 write(p/'EXIT.json',dict(returncode=0,automatic_retry=False,requires_independent_acceptance=True))
except Exception as e:
 write(p/'EXIT.json',dict(returncode=1,error=str(e),automatic_retry=False,outcome='inspect native receipt; never rerun automatically'))
 raise
