"""Explicit independent D+1 synthetic-rule reuse, never automatic renewal."""
from clock_adapter import *
import agc_lifecycle as original
SCENARIO='D+1_same_declared_synthetic_rules_five_thermal_qualifications_and_bids'
def payload(*,scenario,available_at):
 need(scenario==SCENARIO,'explicit named nextday research scenario required');source=original.B/'agc_runtime/RESEARCH_INPUT.json';need(sha(source)==original.INPUT_SHA,'original synthetic declaration drift');d=read(source)
 ends={x['effective_to']for x in d['parameters'].values()};need(len(ends)==1,'mixed original expiry unsupported');begin=stamp(next(iter(ends)));end=begin+timedelta(days=1);need(stamp(available_at)<=begin-timedelta(minutes=30),'declaration unavailable at H24 deadline')
 for x in d['parameters'].values():x.update(effective_from=begin.isoformat(),effective_to=end.isoformat())
 d['research_extension']=dict(scenario=scenario,source=str(source),source_sha256=sha(source),original_effective_to=begin.isoformat(),new_effective_from=begin.isoformat(),new_effective_to=end.isoformat(),available_at=available_at,units_sha256=digest(d['units']),constructor_sha256=sha(__file__),meaning='explicit independent research assumption; same synthetic five thermal qualification declarations and bids, not actual qualification or official rules',formal=False,safety_release=False,actual_dispatch_authorized=False)
 return d
def declare(output,*,scenario,available_at):
 d=payload(scenario=scenario,available_at=available_at);write(Path(output),d);return d
def validate(path,*,cutoff):
 d=read(path);x=d.get('research_extension',{});need(bool(x),'missing explicit nextday research declaration');need(d==payload(scenario=x['scenario'],available_at=x['available_at']),'changed nextday declaration');need(stamp(x['available_at'])<=stamp(cutoff),'nextday declaration not available');return d
def bind_to_live_host(host,path):
 from unified import RollingHost
 need(isinstance(host,RollingHost),'live registered host required');host.session.verify_sources();cp=Path(host.cp);need(str(cp)in host.hooks.carries and sha(cp)==host.hooks.carries[str(cp)],'live carry drift');path=Path(path).resolve();validate(path,cutoff=read(cp)['cutoff'])
 host.session.registry['source_pins'].update({str(path):sha(path),str(Path(__file__).resolve()):sha(__file__)})
 return path
