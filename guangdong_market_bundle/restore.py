"""Restore the complete public research package after verifying every part."""
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import sys
import tarfile

here = Path(__file__).resolve().parent
manifest = json.loads((here / 'package_manifest.json').read_text(encoding='utf-8'))
parts = []
for spec in manifest['parts']:
    data = (here / spec['name']).read_bytes()
    if len(data) != spec['size'] or hashlib.sha256(data).hexdigest() != spec['sha256']:
        raise SystemExit('Part verification failed: ' + spec['name'])
    parts.append(data)
data = b''.join(parts)
if len(data) != manifest['size'] or hashlib.sha256(data).hexdigest() != manifest['sha256']:
    raise SystemExit('Package verification failed')
target = here.parent / 'guangdong_market'
if target.exists():
    raise SystemExit('Refusing to overwrite existing directory: ' + str(target))
extract_root = str(here.parent.resolve())
if os.name == 'nt':
    extract_root = '\\\\?\\' + extract_root
with tarfile.open(fileobj=io.BytesIO(data), mode='r:xz') as archive:
    for member in archive.getmembers():
        p = PurePosixPath(member.name)
        if p.is_absolute() or '..' in p.parts or not p.parts or p.parts[0] != 'guangdong_market' or not (member.isfile() or member.isdir()):
            raise SystemExit('Unsafe archive member: ' + member.name)
    if sys.version_info >= (3, 12):
        archive.extractall(extract_root, filter='data')
    else:
        archive.extractall(extract_root)
print('Verified and restored:', target)
print('Read guangdong_market/使用说明.md before running the research application.')
