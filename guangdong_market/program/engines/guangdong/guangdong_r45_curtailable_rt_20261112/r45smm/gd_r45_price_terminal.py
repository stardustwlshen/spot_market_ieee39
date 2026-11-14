"""R22 timestamped PRICE endpoint semantics, no relaxation of physical bounds."""
from .gd_r15_da_rt_bridge import require
from .gd_r45_source_contract import TARGETS
POLICY=dict(schema='gd.research.r45.timestamp_PRICE_terminal.v1',non_day_end='endogenous explicitly research',D96='original hard timestamp, including local k24',formal_strict=False,clipping=False)
def terminal_trace(inp,formal_strict=False,declared_parameters=None):
    require(formal_strict is False and declared_parameters is None,'formal declared parameters unavailable')
    require(inp['midnight_targets']==TARGETS,'original targets')
    k=inp['window']['midnight_local_energy_index']
    return {r:dict(effective_terminal_mwh=None,rolling_equality=False,D96_local_index=k,D96_energy_mwh=v if k is not None else None) for r,v in TARGETS.items()}
