"""Unified portable router. Explicit solver flags pass through; no hidden execution."""
from pathlib import Path
import subprocess,sys
R=Path(__file__).resolve().parent
if len(sys.argv)<3 or sys.argv[1]not in ('rt','price'):raise SystemExit('usage: user_entry.py rt|price ACTION ...')
p=R/'rt/user_entry.py'if sys.argv[1]=='rt'else R/'price/program.py'
raise SystemExit(subprocess.call([sys.executable,'-B',str(p),*sys.argv[2:]]))
