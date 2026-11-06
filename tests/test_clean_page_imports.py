"""The documented direct Streamlit command must not need a hidden src path."""
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('page', ['2_运行.py', '3_结果.py', '4_场景对比.py'])
def test_page_imports_bootstrap_src_before_using_smm(page, tmp_path):
    code = '''
import ast, pathlib, sys
root = pathlib.Path(sys.argv[1])
sys.path = [p for p in sys.path if pathlib.Path(p).resolve() not in (root/'src', root/'app')]
assert 'smm' not in sys.modules
sys.path.insert(0, str(root/'app'))
source = root/'app/pages'/sys.argv[2]
tree = ast.parse(source.read_text())
imports = []
for node in tree.body:
    if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call) and getattr(node.value.func, 'id', '') == 'page_setup':
        break
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        imports.append(node)
exec(compile(ast.Module(body=imports, type_ignores=[]), str(source), 'exec'), {'__file__': str(source)})
import smm.time_grid
assert pathlib.Path(smm.time_grid.__file__).resolve() == root/'src/smm/time_grid.py'
'''
    result = subprocess.run([sys.executable, '-c', code, str(ROOT), page],
                            cwd=tmp_path, env=os.environ.copy(), capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
