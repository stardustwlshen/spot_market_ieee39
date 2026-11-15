"""Research short-domain boundary support; never claims official terminal certification."""
from datetime import datetime,timedelta
from battery_short import validate_batteries,_attach_batteries

def validate_contract(c,n):
 if c['schema']!='gd.short.physical.contract.v1' or c['mode']!='research':raise ValueError('formal short compiler blocked: official linkage/terminal details unresolved')
 if n not in(24,48):raise ValueError('24 core or research joint48')
 read=lambda x:datetime.fromisoformat(x)
 for k in('origin','cutoff','domain_start','publication_at'):
  if read(c[k]).utcoffset()!=timedelta(hours=8):raise ValueError('absolute UTC+8 clock')
 if read(c['publication_at'])>read(c['cutoff']):raise ValueError('future published source')
 from stage_contract import validate_clock
 validate_clock(c,n)
 if read(c['domain_start'])!=read(c['origin'])+timedelta(minutes=5*c['stage']['start_tick']):raise ValueError('this compiled candidate chooses delivery anchor once, not official adjudication')
 if not c['clock_interpretation_source']or c['near_far_mode']!='sequential24_predicted_boundary_research':raise ValueError('explicit chosen policy')
 return dict(schema=c['schema'],day_start=c['domain_start'],cutoff=c['cutoff'],publication_at=c['publication_at'],physical_count=n,origin=c['origin'],formal_ready=False,economic_policy='only_selected_short_domain_energy_and_explicit_inside_domain_events',terminal_policy='residual_obligations_exported; no global network certificate',grid=[dict(index=t,start=(read(c['domain_start'])+timedelta(minutes=5*t)).isoformat())for t in range(n)])
