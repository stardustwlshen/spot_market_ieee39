"""Default4/explicit96 backend adapter. No renewal, solve or restart by default."""
from pathlib import Path
import sys,json
B=Path(__file__).resolve().parent;R=B.parent
sys.path.insert(0,str(R/'guangdong_research_stop_permission_v1_20261113'))
from permissions import StopPermissionHost,e,sha,read
sys.path.insert(0,str(R/'guangdong_day_clock_candidate_v1_20261113'))
from clock_adapter import DayClockAdapter,schedule
from nextday_declaration import validate,SCENARIO

def plan(windows=4):
 z=schedule(windows);z['capability_blockers']=['H24 requires explicit source-pinned D+1 synthetic declaration, not automatic qualification renewal','changed H24 projections require new W94/W95 mathematical acceptance','96window General/StopPermission end-to-end native validation not completed'];z['numeric_normalization']='explicit <=1e-10 receipt via GeneralEventHooks, no silent clip';return z

def select_existing_declaration(path,*,scenario):
 e.need(scenario==SCENARIO,'explicit named D+1 research selection required')
 path=Path(path).resolve();d=read(path);x=d.get('research_extension',{});e.need(x.get('scenario')==scenario,'different scenario')
 # Selection does not mean consumption. Actual availability rechecked at H24.
 validate(path,cutoff=x['available_at']);return path

class ClockedStopPermissionHost(StopPermissionHost):
 def __init__(self,*args,windows=4,nextday_agc_declaration=None,nextday_scenario=None,**kw):
  plan(windows);self.windows=windows;self._day_clock=None
  e.need((nextday_agc_declaration is None)==(nextday_scenario is None),'declaration and explicit scenario must be selected together')
  declaration=None if nextday_agc_declaration is None else select_existing_declaration(nextday_agc_declaration,scenario=nextday_scenario)
  super().__init__(*args,**kw)
  if declaration is not None:self.session.registry['source_pins'][str(declaration)]=sha(declaration)
  # Core clock keeps the same accepted-source callback and live carry registries.
  self._day_clock=DayClockAdapter(self,windows=windows,nextday_agc_path=declaration)
  import clock_adapter,nextday_declaration
  for f in (Path(__file__),Path(clock_adapter.__file__),Path(nextday_declaration.__file__)):self.session.registry['source_pins'][str(f.resolve())]=sha(f)
 def prepare(self):
  e.need(self._day_clock is not None,'clock not initialized');builder=self.session.builder;original=builder.serialize_model;trace=[]
  def guarded(model,options):
   trace.extend(self.stop_permissions.apply(model));return original(model,options)
  builder.serialize_model=guarded
  try:result=self._day_clock.prepare()
  finally:builder.serialize_model=original
  p=self.pending;options=read(p/'template.json')['options'];e.need(options['threads']==1 and options['mip_rel_gap']==.01 and options['time_limit']==3600.,'unchanged solver budget')
  f=p/'STOP_PERMISSION_BINDING.json';f.write_text(json.dumps(dict(commands=self.stop_permissions.read(),applied=trace),sort_keys=True));self.hooks.prepared[str(p.resolve())]['artifact_pins'][str(f)]=sha(f)
  return result
 def read(self):
  return dict(super().read(),windows=self.windows,clock_plan=plan(self.windows),nextday_automatic_renewal=False,full_day_native_validated=False)
