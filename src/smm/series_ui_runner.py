"""Private worker entry point for explicitly confirmed task launches."""
from pathlib import Path
import json,sys
from .series_workspace import atomic_json
from .series_ui_adapter import compile_days,verify_source,prepared_causal_bundle


def main(root,manifest,mode):
    from .series import run_series
    from .state_bridge import unpack_state
    root=Path(root);m=json.loads(Path(manifest).read_text(encoding='utf-8'))
    try:
        verify_source(root,m);days=compile_days(root,m)
        initial=unpack_state(days[0].cfg,m['initial_state'])
        source_bundle=prepared_causal_bundle(root,m)[2] if m['generation']['model']=='causal_physical_v3' else None
        result=run_series(days,root/'run',initial_actual_state=initial,mode=mode,source_bundle=source_bundle,result_retention='archive_only',
            should_cancel=lambda:(root/'cancel_after_day.request').exists(),log=lambda s:print(s,flush=True))
        atomic_json(root/'worker_outcome.json',{k:v for k,v in result.items() if k not in ('new_results','checkpoints','financial')})
    except BaseException as exc:
        atomic_json(root/'worker_outcome.json',dict(status='failed',error=str(exc)))
        raise

if __name__=='__main__':main(*sys.argv[1:])
