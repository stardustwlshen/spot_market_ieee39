"""Build existing micro connector without native calls; apply contract to real free builder."""
from pathlib import Path
import sys,ast,json,hashlib
B=Path(__file__).resolve().parent;R=B.parent
sys.path.insert(0,str(R/'guangdong_thermal_notice_bridge_v2_20261113'))
from contract import apply_to_model
source=R/'guangdong_thermal_notice_bridge_v2_20261113/run_connected_micro.py'
s=source.read_text();prefix=s.split('q,vs,cs=serialize_model(',1)[0]
assert len(prefix)<len(s) and 'worker.native(' not in prefix and '.mkdir(' not in prefix and 'write_json(' not in prefix
# Reviewed construction-only prefix retains existing conservative protection.
ns={'__name__':'pure_builder_check'};exec(compile(prefix,str(source)+'#construction-only','exec'),ns)
c=json.loads((B/'COMPILED_R68.json').read_text());z=apply_to_model(ns['model'],c)
assert z['rows']==12
q,vs,cs=ns['serialize_model'](ns['model'],ns['validate_options'](dict(time_limit=30.,threads=1,mip_rel_gap=.001,mip_abs_gap=0.,output_flag=False)))
import gzip
n=json.loads((R/'guangdong_thermal_notice_bridge_v2_20261113/connected_second_window/names.json').read_text());v=json.loads(gzip.decompress((R/'guangdong_thermal_notice_bridge_v2_20261113/connected_second_window/SCUC/response.json.gz').read_bytes()))['primal'];old=dict(zip(n['variables'],v));x=[old[a.name]for a in vs]
rows=[(i,r)for i,r in enumerate(cs)if r.name.startswith('dispatch_contract_rows[')]
err=max(abs(sum(a*x[j]for j,a in q['rows'][i]['terms'])-q['rows'][i]['lb'])for i,r in rows)
assert err<1e-7
result=dict(status='real_free_builder_constructed_and_12_contract_rows_serialized_existing_native_satisfies',new_native_calls=0,added_rows=z['rows'],max_added_row_residual=err,construction_source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),source_prefix_only=True,conservative_protection_retained=True,full_case_integration=False,formal=False)
(B/'REAL_BUILDER_RESULT.json').write_text(json.dumps(result,indent=2));print(json.dumps(result))
