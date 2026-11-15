"""Fresh source-bound initial base only. Never initializes a solver."""
from pathlib import Path
import sys,importlib.util,json
B=Path(__file__).resolve().parent
if __name__=='__main__':
 p=B/'research_runtime/single_window/worker/new_base.py'
 spec=importlib.util.spec_from_file_location('_current_dc_base_factory',p);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
 z=m.initialize_new_research_base(program_root=B.parent,selected_DA=sys.argv[1],day_declarations=sys.argv[2],output=sys.argv[3],research=True)
 print(json.dumps(z))
