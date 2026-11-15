"""User-selected RT reference. Do not relabel the historical DA seed as RT."""
import math
from copy import deepcopy


def assert_reference_alignment(snapshot, specs, state):
    if snapshot['window'] < 0 and not snapshot.get('initial_rt_sced_verified'):
        raise ValueError('q0 requires an actual tick-0 RT SCED; DA seed is not an RT reference')
    first = snapshot['request']['delivery_tick']
    if type(first) is not int or first < 0:
        raise ValueError('PRICE reference must cover one aligned fifteen-minute cell')
    values = snapshot['values']
    for column, spec in specs.items():
        unit, quarter = column[2:-1].rsplit(',', 1)
        q = int(quarter)
        cell_values = [values[f'power[{unit},{t}]'] for t in range(first+q,first+q+1)]
        if any(not math.isfinite(v) for v in cell_values):
            raise ValueError('nonfinite RT SCED source')
        if abs(math.fsum(cell_values)-spec['current_sced']) > 1e-8:
            raise ValueError('PRICE reference differs from same-period RT SCED cell')
        if spec['status'] != state['eligibility'][unit][q]:
            raise ValueError('PRICE integer-state classification differs from RT SCED')
    return True


def preserve_current_ramps(model, names, specs, *, policy):
    if policy != 'current_SCED_incoming_state':
        raise ValueError('explicit RT ramp policy required')
    # domain_event built these rows from the actual incoming RT state already.
    # Old DA-first-ramp overlay must not replace that state.
    return deepcopy(model), [dict(policy=policy,action='retained_domain_event_current_state_rows',formal=False)]
