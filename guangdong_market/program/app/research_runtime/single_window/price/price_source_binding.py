"""Bind PRICE preparation to the existing host selection, not new authority."""
from pathlib import Path

def need(ok, message):
    if not ok:
        raise ValueError(message)

def snapshot(session):
    """Only called after the existing session.verify_sources guard."""
    reg = session.registry
    pins = dict(reg['source_pins'])
    run = str(Path(session.run).resolve())
    context = str(Path(run) / 'SHORT_CONTEXT.json')
    need(context in pins, 'host selection lacks registered context pin')
    need(pins and all(isinstance(p, str) and isinstance(h, str) and len(h) == 64 for p, h in pins.items()), 'invalid host source pins')
    return dict(schema='gd.PRICE.existing_host_selection.v1',
                run_id=reg['run_id'], case_identity=reg['case_identity'],
                base_run=run, source_pins=pins)

def verify(binding, session):
    expected = snapshot(session)
    need(binding == expected, 'PRICE prepared for a different host/base/source selection')
    # Caller already ran the existing session.verify_sources byte checks.
    # Do not repeat large file hashing or create independent authority here.
    return True
