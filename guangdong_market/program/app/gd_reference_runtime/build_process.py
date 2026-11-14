"""Pure model process. JSON input supplies math only, never live authority."""
from pathlib import Path
import sys,json
B=Path(__file__).resolve().parent;APP=B.parent;R=APP.parent/'engines/guangdong_short_unified_v6/rt'
sys.path[:0]=[str(B),str(R/'guangdong_rt_short_user_cli_v5_20261112'),str(R/'guangdong_rt_short_midnight_v1_20261112')]
import control_source,builder
from reference_math import fix_original_da_integers
read=lambda p:json.loads(Path(p).read_bytes())
out=Path(sys.argv[1]);z=read(out/'BUILD_INPUT.json');control_source.CONFIG=z
builder.build(out/'seed',out/'INITIAL_CARRY.json',out/'prepared')
q,m=fix_original_da_integers(read(out/'prepared/template.json'),read(out/'prepared/names.json'),z['values'])
assert all(abs(z['scuc'][r['source_name']]-r['value'])<=1e-8 for r in m),'DA SCUC/SCED integer mismatch'
q['options'].update(time_limit=3600.,solver='simplex',presolve='off',dual_feasibility_tolerance=1e-10,primal_feasibility_tolerance=1e-9,simplex_scale_strategy=0,run_crossover='off',ipm_optimality_tolerance=1e-12)
(out/'prepared/REFERENCE_LP.json').write_text(json.dumps(q));(out/'INTEGER_SOURCE.json').write_text(json.dumps(m));print('PURE_REFERENCE_READY',len(m))
