"""Explicit provincial fifteen-minute horizon; two-hour window is research setting."""
from datetime import datetime,timedelta
from .battery_short import validate_batteries,_attach_batteries

def validate_contract(c,n):
 mode=c.get('rt_clock_mode')
 if not ((mode=='provincial_15min_execution' and type(n)is int and 1<=n<=8) or (mode=='provincial_15min_day_ahead' and n in (96,98))):raise ValueError('explicit provincial RT1..8 or DA96/98 horizon required')
 read=lambda x:datetime.fromisoformat(x)
 for k in ('origin','cutoff','domain_start','publication_at'):
  if read(c[k]).utcoffset()!=timedelta(hours=8):raise ValueError('UTC+8 clock required')
 if read(c['publication_at'])>read(c['cutoff']):raise ValueError('future input')
 if mode=='provincial_15min_execution':
  if read(c['domain_start'])!=read(c['cutoff']):raise ValueError('chosen same-period simulation clock required')
  elapsed=(read(c['domain_start'])-read(c['origin'])).total_seconds()/900
  if elapsed!=int(elapsed) or not 0<=elapsed<96 or n!=min(8,96-int(elapsed)):raise ValueError('single-day exact truncated horizon required')
 return dict(schema=c['schema'],day_start=c['domain_start'],cutoff=c['cutoff'],publication_at=c['publication_at'],physical_count=n,origin=c['origin'],formal_ready=False,grid_minutes=15,economic_policy='quarter-hour bids and exact event charges',terminal_policy='explicit user boundary assumptions',grid=[dict(index=t,start=(read(c['domain_start'])+timedelta(minutes=15*t)).isoformat())for t in range(n)])
