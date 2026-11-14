from pathlib import Path
import os

def dependency_root():
 return Path(os.environ.get('GD_RECOMMIT_DEPENDENCY_ROOT',str(Path(__file__).resolve().parents[1]/'vendor'))).resolve()
