"""Pure bounded index plan; never authorizes a host, solver or next-day declaration."""
def need(x,m):
 if not x:raise ValueError(m)
def plan(windows=4):
 need(type(windows)is int and windows in (4,96),'only explicit first-hour/full-day scope')
 return dict(windows=windows,hours=windows//4,source_windows=list(range(-1,windows-1)),SCUC_calls=windows,SCED_calls=windows,PRICE_calls=windows,maximum_native_calls=3*windows,default_native_calls=0,executed_end=3*windows,issued_end=3*(windows+1),automatic_execution=False,publication_allowed=False,requires_day_clock_and_valid_nextday_AGC=windows==96)
def hour(index,windows=4):
 z=plan(windows);need(type(index)is int and 0<=index<z['hours'],'hour outside declared scope')
 first=12*index;end=first+12
 return dict(hour=index,source_windows=list(range(4*index-1,4*index+3)),handoff_window=4*index+3,cells=list(range(first,end)),executed_end=end,issued_end=end+3,excluded_issued_ticks=list(range(end,end+3)),native_calls=0)
