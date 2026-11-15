"""Restore user-selected dispatch network penalty; PRICE has a separate policy."""
def apply_dispatch(case):
    e=case['energy_inputs'];p=e['penalties_yuan_per_mwh'];old={k:p[k] for k in ('line','section')}
    # Explicit current user specification, not Guangdong's published numeric parameter.
    p.update(line=10000.,section=10000.)
    case['dispatch_network_penalty_policy']=dict(yuan_per_mwh=10000.,basis='user_specified_dispatch_network_penalty',previous_legacy_synthetic=old,pricing_penalty_not_modified=True,other_penalties_not_modified=True)
    e['source_notes']['penalties']='User specified dispatch network line/section penalty 10000 yuan/MWh; other input penalties preserved; independent PRICE uses its separate 1000 yuan/MWh setting.'
    from .curtailment_policy import apply
    return apply(case)
