"""Keep all request/state provenance invariant except explicit rolling fields."""
from copy import deepcopy
TOP_ROLLING={'window','cutoff','cutoff_tick','delivery_tick','state'}
STATE_ROLLING={'executed_end_tick','issued_end_tick','receipt_at','receipt_sha256','completed_window','fixed_values','notice_values'}
def handoff_immutables(previous,current):
 if current['window']!=previous['window']+1:raise ValueError('handoff nonsequential window')
 if {k:v for k,v in previous.items()if k not in TOP_ROLLING}!={k:v for k,v in current.items()if k not in TOP_ROLLING}:raise ValueError('handoff immutable source/policy/DA fields changed')
 if {k:v for k,v in previous['state'].items()if k not in STATE_ROLLING}!={k:v for k,v in current['state'].items()if k not in STATE_ROLLING}:raise ValueError('handoff immutable state provenance changed')
 return current
