"""Build-time host registration of parent-selected W44/W45/W46, not a user API."""
from pathlib import Path
import hashlib,json
B=Path(__file__).resolve().parent;R=B.parent
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest();read=lambda p:json.loads(Path(p).read_bytes())
accepted=R/'guangdong_rt_short_user_cli_v10_20261113';root=accepted/'import_W34_recovered_v1';builder=R/'guangdong_rt_short_user_cli_v11_20261113'
pins={str(f):sha(f)for f in [*builder.glob('*.py'),B/'host.py',B/'runtime_hooks.py',*list((R/'guangdong_fixed_event_entry_v1_20261113').glob('*.py')),*list((R/'guangdong_fixed_event_entry_v1_20261113/vendor').glob('*.py'))]}
z=dict(schema='gd.event.host.registry.v1',builder_root=str(builder),accepted_producer_root=str(accepted),seed=str(root/'seed'),case_identity=sha(root/'seed/case.json'),run_id='independent_event_host_W45_W46_v1',source_pins=pins,windows={})
for w in (44,45,46):
 p=root/f'W{w}/prepared';a=p.parent/'actual';z['windows'][str(w)]=dict(prepared=str(p),actual=str(a),summary_sha256=sha(p/'SUMMARY.json'),acceptance_sha256=sha(a/'ACCEPTED_PAIR.json'),available_at=read(p/'SUMMARY.json')['domain_start'],next_carry=str(root/f'handoff/W{w+1}/CARRY.json'))
(B/'HOST_REGISTRY.json').write_text(json.dumps(z,indent=2)+'\n');(B/'trusted_host.py').write_text('# Host build-time trust anchor; no user-provided JSON may change it.\nREGISTRY_SHA256='+repr(sha(B/'HOST_REGISTRY.json'))+'\n')
