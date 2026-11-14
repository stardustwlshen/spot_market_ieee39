"""Confine reference lookup paths; never retain its colliding RT compiler names."""
from contextlib import contextmanager
from pathlib import Path
import sys
_COLLISIONS=('thermal_physical','short_support','battery_short','builder','control_source')
def _reference_module(module,root):
 f=getattr(module,'__file__',None)
 return bool(f)and Path(f).resolve().is_relative_to(root)
@contextmanager
def reference_import_scope(app,*,expose=False):
 root=(Path(app)/'gd_reference_runtime').resolve();before_path=list(sys.path)
 before={name:sys.modules.get(name)for name in _COLLISIONS}
 for name,module in before.items():
  if module is not None and _reference_module(module,root):raise RuntimeError('reference compiler leaked before scoped entry: '+name)
 if expose:sys.path.insert(0,str(root))
 try:yield
 finally:
  sys.path[:]=before_path
  for name,previous in before.items():
   current=sys.modules.get(name)
   if current is not previous and current is not None and _reference_module(current,root):
    if previous is None:sys.modules.pop(name,None)
    else:sys.modules[name]=previous
