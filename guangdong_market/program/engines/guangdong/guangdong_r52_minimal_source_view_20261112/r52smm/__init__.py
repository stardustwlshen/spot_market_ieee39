"""Pure R52 source-view overlay; unchanged R46 modules remain pinned dependencies."""
from pathlib import Path
R=Path(__file__).resolve().parents[2]
__path__.extend([str(R/'guangdong_r46_curtailable_full_day_20261112/r46smm'),str(R/'guangdong_r21_day_verified_final_v1_20261112/source/repo/src/smm')])
