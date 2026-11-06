"""One seed, three days, 121 issued windows. No optimization or accuracy claim."""
import sys,time,json,tracemalloc,resource
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from smm.config import load_config
from smm.causal_provider import resources_from_config,compile_provider
from smm.causal_processes import ProcessPlan
from smm.causal_types import utc_seconds,utc_iso
cfg=load_config(); specs=resources_from_config(cfg)
start='2026-07-15T00:00:00Z'
t=time.perf_counter(); actual,provider=compile_provider(ProcessPlan(start,3,'UTC',specs,root_seed='42'))
generation=time.perf_counter()-t;t=time.perf_counter()
for k in range(121):
    day,kday=divmod(k,96)
    provider.select_day(utc_iso(utc_seconds(start)+day*86400),kday,kday,stage='RT',
                        current_interval=True,executed_through_period=kday)
result=dict(model=provider.model_version,seed='42',days=3,resources=len(specs),windows=121,
    generation_seconds=generation,publication_seconds=time.perf_counter()-t,
    peak_rss_mb=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024,
    filter_stats=provider.forecaster.stats,actual_hash=actual.content_hash,
    scope='generator_and_121_publications_not_solver_or_accuracy_acceptance')
print(json.dumps(result,ensure_ascii=False,indent=2))
