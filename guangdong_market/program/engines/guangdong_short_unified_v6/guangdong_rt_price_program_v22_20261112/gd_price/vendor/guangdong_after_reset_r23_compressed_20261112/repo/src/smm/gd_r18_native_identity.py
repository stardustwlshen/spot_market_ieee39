"""Read-only runtime identity; imports no solver and starts no native library."""
from hashlib import sha256
import importlib.metadata
import json
from pathlib import Path
import sys


def runtime_identity():
    def hashed(path):
        h = sha256()
        with Path(path).open('rb') as f:
            for block in iter(lambda: f.read(1024 * 1024), b''):
                h.update(block)
        return h.hexdigest()
    packages = {}
    for name in ('highspy', 'pyomo', 'numpy'):
        dist = importlib.metadata.distribution(name)
        files = {str(p): hashed(dist.locate_file(p)) for p in sorted(dist.files, key=str)
                 if str(p).endswith(('.py', '.so', '.pyd', '.dll', '.dylib'))}
        packages[name] = dict(version=dist.version, executable_file_count=len(files),
            executable_files_sha256=sha256(json.dumps(files, sort_keys=True, separators=(',', ':')).encode()).hexdigest())
    executable = Path(sys.executable).resolve()
    return dict(executable=str(executable), executable_sha256=hashed(executable),
        python_version=sys.version, packages=packages)
