"""Pure bounded index plan; never authorizes a host, solver or next-day declaration."""
def need(x,m):
 if not x:raise ValueError(m)
def plan(windows=4):
 need(type(windows)is int and windows in (4,96),'only explicit first-hour/full-day scope')
 return dict(windows=windows,hours=windows//4,source_windows=list(range(windows)),SCUC_calls=windows,SCED_calls=windows,PRICE_calls=windows,maximum_native_calls=3*windows,default_native_calls=0,executed_end=windows,issued_end=windows,grid_minutes=15,automatic_execution=False,publication_allowed=False,requires_day_clock_and_valid_nextday_AGC=False,single_day_scope='truncate_at_24h',day_end_tick=96)
def hour(index,windows=4,*,same_period_execution=False):
 need(same_period_execution,'province hour requires same-period execution');z=plan(windows);need(type(index)is int and 0<=index<z['hours'],'hour outside declared scope')
 first=4*index;end=first+4
 return dict(hour=index,source_windows=list(range(4*index,4*index+4)) if same_period_execution else list(range(4*index-1,4*index+3)),handoff_window=4*index+3,cells=list(range(first,end)),executed_end=end,issued_end=end if same_period_execution else end+3,excluded_issued_ticks=[] if same_period_execution else list(range(end,end+3)),native_calls=0)
