"""Bind an explicitly installed, byte-matching2.2 package; no installer or native solve."""
from pathlib import Path
import argparse,json,hashlib
B=Path(__file__).resolve().parent
p=argparse.ArgumentParser();p.add_argument('--installation-root',required=True);p.add_argument('--python',required=True);p.add_argument('--output',required=True);a=p.parse_args();r=Path(a.installation_root).resolve();py=Path(a.python).absolute();assert py.is_file();pins=json.loads((B/'EXPECTED_PACKAGE_HASHES.json').read_text())
for f,h in pins.items():
 if hashlib.sha256((r/f).read_bytes()).hexdigest()!=h:raise ValueError('不是已验2.2安装源码：'+f)
with Path(a.output).open('x')as f:json.dump(dict(schema='gd.price.ui.installed_profile.v1',version='2.2.0',python=str(py),installation_root=str(r),package_pins=pins),f,indent=2)
print('安装路径已绑定，0次求解')
