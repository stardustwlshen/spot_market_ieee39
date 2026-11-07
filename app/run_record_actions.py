"""Read-only run-card timing and explicit navigation to one saved result."""
from datetime import datetime
from math import isfinite
from numbers import Real
from pathlib import Path

import streamlit as st

ACTIVE_STATES = {'starting', 'running', 'cancelling'}


def elapsed_for_display(meta, *, now=None):
    """Return (seconds, estimated), never invent a terminal runtime.

    Active cards show wall time since the recorded launch request, not pure
    optimizer time. No metadata is written and invalid/future times stay unknown.
    """
    recorded = meta.get('elapsed_s')
    valid = (isinstance(recorded, Real) and not isinstance(recorded, bool)
             and isfinite(recorded) and recorded >= 0)
    if meta.get('status') not in ACTIVE_STATES:
        return (float(recorded), False) if valid else (None, False)
    started = meta.get('started')
    if isinstance(started, str) and started:
        try:
            start = datetime.fromisoformat(started.replace('Z', '+00:00'))
            current = now if now is not None else datetime.now(tz=start.tzinfo)
            # Tests may supply an aware or naive clock; do not silently guess
            # timezone conversions across copied or malformed run records.
            seconds = (current - start).total_seconds()
            if isfinite(seconds) and seconds >= 0:
                return seconds, True
        except (ValueError, TypeError, OverflowError):
            pass
    return (float(recorded), False) if valid else (None, False)


def has_saved_result(meta):
    """An openable completed scope, not a claim of full-day settlement."""
    from result_catalog import is_single_day_result
    if meta.get('status') != 'done' or not is_single_day_result(meta):
        return False
    directory = Path(meta['dir'])
    return ((directory / 'summary.json').is_file()
            or (meta.get('mode') == 'da' and (directory / 'da/summary.json').is_file()))


def render_open_saved_result(meta):
    """Explicit request only; never silently replace a user's historical view."""
    if not has_saved_result(meta):
        return
    directory = str(meta['dir'])
    if st.button('查看本次结果', key='open_saved_result:' + directory,
                 help='打开这次运行已保存的结果，不修改当前场景或任何历史文件。'):
        st.session_state['result_sel'] = directory
        st.switch_page('pages/3_结果.py')
