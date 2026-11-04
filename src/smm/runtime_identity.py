"""Read-only source identity shared by local bootstrap and the web interface.

User scenes, quotations and results are deliberately excluded: editing inputs is
not a software upgrade. This module must remain standard-library-only.
"""
from __future__ import annotations

import hashlib
from pathlib import Path


def source_digest(root: Path) -> str:
    root = Path(root)
    digest = hashlib.sha256()
    paths = [root / 'requirements-app.txt']
    for folder in ('app', 'src', 'scripts', 'config', '.streamlit'):
        paths.extend(p for p in (root / folder).rglob('*')
                     if p.is_file() and '__pycache__' not in p.parts
                     and p.suffix in ('.py', '.yaml', '.toml')
                     and p.name != 'secrets.toml')
    for path in sorted(paths):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()
