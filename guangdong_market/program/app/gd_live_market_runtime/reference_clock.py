"""Post-reference pricing availability distinct from SCUC data cutoff."""
from datetime import datetime,timedelta

def check(source,reference):
 if not reference.get('post_SCUC_reference'):return None
 def need(ok,msg):
  if not ok:raise ValueError(msg)
 request=source['request'];cut=datetime.fromisoformat(request['cutoff']);available=cut+timedelta(minutes=15)
 need(source['window']==94 and request['delivery_tick']==285,'post reference only q95')
 need(reference['data_as_of']==cut.isoformat(),'post reference data clock differs from SCUC cutoff')
 need(reference['stage_available_at']==available.isoformat()and reference['input_available_at']==available.isoformat(),'post reference must be available at q95 start23:45')
 need(source['executed_end']>=285 and source['issued_end']>=288,'post reference requires accepted W94 exact handoff')
 return dict(SCUC_data_cutoff=cut.isoformat(),pricing_input_available_at=available.isoformat(),reference_data_as_of=reference['data_as_of'],not_retroactively_available=True)
