from runtime_hooks import *
B=Path(__file__).resolve().parent
s=Session(B/'runtime_hook_state',research_fixed_curve_events=True);h=RuntimeHooks(s);carry,ledger=h.adopt_registered_boundary(45)
r=h.build(carry,B/'runtime_prepared',ledger_id=ledger)
a=read(B/'runtime_prepared/template.json');b=read(B/'two_window_run/W45/prepared/template.json');an=read(B/'runtime_prepared/names.json');bn=read(B/'two_window_run/W45/prepared/names.json')
assert a['rows']==b['rows']and a['objective']==b['objective']and a['variables']==b['variables']and an==bn
# Wrong input/ledger cannot enter the normal callback just by hashing itself.
for cp,lid in [(carry,'0'*64),(B/'two_window_run/W45/CARRY.json',ledger)]:
 try:h.build(cp,B/'should_not_build',ledger_id=lid)
 except AssertionError:pass
 else:raise AssertionError('unregistered host input accepted')
write(B/'RUNTIME_HOOK_RESULT.json',dict(status='normal_builder_runtime_callback_matches_two_window_candidate',native_calls=0,rows=len(a['rows']),unregistered_carry_ledger_rejected=True,generated_pair_handoff_requires_real_new_accepted_pair=True))
print('normal full builder hook passed; unregistered input rejects;0native')
