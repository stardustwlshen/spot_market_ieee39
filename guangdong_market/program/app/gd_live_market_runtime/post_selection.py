"""Bounded first-hour planning selection; no token or award minted here."""
from pathlib import Path
from source import e,sha,read
from datetime import datetime,timedelta
import json,hashlib
DEFAULT=Path(__file__).resolve().parent.parent/'lifecycle_runtime/guangdong_unified_event_agc_host_v1_20261113/agc_runtime/RESEARCH_INPUT.json'
def default_declaration(session):
 origin=datetime.fromisoformat(read(session.run/'seed/request.json')['origin']);data=read(DEFAULT);data.update(hour_start=(origin+timedelta(hours=1)).isoformat(),as_of=(origin+timedelta(minutes=30)).isoformat())
 case=session.run/'seed/case.json';data['research_binding']=dict(run=str(session.run),selected_DA=session.context['selected_DA'],case_sha256=sha(case),template_sha256=sha(DEFAULT),template_source=str(DEFAULT),meaning='explicit original synthetic qualification/parameter template; times derived from selected run, no automatic validity renewal or real eligibility certification')
 tag=hashlib.sha256(json.dumps(data,sort_keys=True).encode()).hexdigest()[:20];directory=session.run.parent/'post_scuc_research_declarations';directory.mkdir(exist_ok=True);p=directory/('H1_'+tag+'.json')
 if p.exists():e.need(read(p)==data,'default declaration changed')
 else:
  with p.open('x')as f:json.dump(data,f,indent=2)
 return p
def preflight(session,*,enabled=False,declarations=None,windows=4,reference_revision=False):
 e.need(type(enabled)is bool,'explicit planning boolean')
 if not enabled:
  e.need(not declarations,'post declarations without independent mode');return None
 e.need(windows==4,'post-SCUC saturated planning currently only4-window scope')
 e.need(not reference_revision,'post-SCUC planning and289 reference revision are incompatible: H24 receipt timing requires separate adapter')
 session.verify_sources();paths=[str(Path(p).resolve())for p in (declarations or [default_declaration(session)])]
 from gd_post_scuc_runtime.controller import preflight as check
 result=check(session.run,paths,saturated_planning=True)
 e.need(len(result['hours'])==1 and result['hours'][0]['hour']==1,'first-hour mode requires exactly next H1 declaration, no future-hour extension')
 return dict(declarations=paths,scope=result,source_pins={str(p):sha(p)for p in paths},source='explicit original five-qualified-plant saturated50 planning, not published awards',native_calls=0)
