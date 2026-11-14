from event_path import *
B=Path(__file__).resolve().parent;R=B.parent
sys.path[:0]=[str(R/'guangdong_rt_short_user_cli_v10_20261113'),str(R/'guangdong_rt_short_midnight_v1_20261112')]
import builder_user_v2 as builder
bundle=read(B/'W45_BUNDLE.json');case=unpack(bundle['case']);ledger=read(B/'W45_LEDGER.json');serialize=builder.serialize_model
class Done(Exception):pass
result={}
def hook(model,options):
 q,vs,cs=serialize(model,options);out=apply_ledger_to_free_model(model,case,ledger,host_case_identity=bundle['case_identity'],run_id=bundle['run_id'],expected_ledger_sha256=ledger['ledger_sha256']);new,nv,nc=serialize(model,options)
 assert [v.name for v in vs]==[v.name for v in nv] and q['objective']==new['objective']
 byname=dict(zip([c.name for c in nc],new['rows']));assert all(byname[c.name]==row for c,row in zip(cs,q['rows']))
 x=bundle['SCED_response']['primal'];rows=[row for name,row in byname.items()if name.startswith('fixed_event_ledger_rows[')];err=max(abs(math.fsum(v*x[j]for j,v in row['terms'])-row['lb'])for row in rows);assert err<1e-7
 result.update(out,real_IEEE39_builder=True,old_rows_objective_retained=True,old_accepted_primal_added_residual=err);raise Done
builder.serialize_model=hook
root=R/'guangdong_rt_short_user_cli_v10_20261113/import_W34_recovered_v1'
try:builder.build(root/'seed',root/'W45/prepared/CARRY.json',B/'SHOULD_NOT_BE_WRITTEN')
except Done:pass
else:raise AssertionError('construction hook missed')
(B/'MODEL_HOOK_RESULT.json').write_text(json.dumps(result,indent=2));print(result)
