"""Private hosted launch preserves protections and checks its persistent volume."""
from pathlib import Path
from types import SimpleNamespace

from scripts import launch_sichuan


def _host(monkeypatch, tmp_path):
    # The deployed file is invoked as a script, with its sibling on sys.path.
    import sys
    monkeypatch.setitem(sys.modules,'launch_sichuan',launch_sichuan)
    from scripts import host_sichuan
    monkeypatch.setattr(host_sichuan,'__file__',str(tmp_path/'scripts/host_sichuan.py'))
    monkeypatch.setattr(host_sichuan,'check',lambda _root: [])
    monkeypatch.setattr(host_sichuan,'os',SimpleNamespace(
        environ={'PORT':'9080'},chdir=lambda _root:None,execv=lambda *_:None))
    return host_sichuan


def test_host_check_rejects_persistent_volume_that_is_a_file(monkeypatch,tmp_path,capsys):
    host=_host(monkeypatch,tmp_path)
    path=tmp_path/'runs_sichuan_v5_consultation'
    path.write_text('do not replace')
    assert host.main(['--check'])==2
    assert path.read_text()=='do not replace'
    assert '不可写' in capsys.readouterr().err


def test_host_uses_same_sichuan_app_with_safe_defaults(monkeypatch,tmp_path):
    host=_host(monkeypatch,tmp_path)
    calls=[]
    host.os.execv=lambda _,cmd:calls.append(cmd)
    assert host.main(['--check'])==0 and not calls
    host.main([])
    host.main(['--container'])
    assert len(calls)==2
    for i,address in enumerate(('127.0.0.1','0.0.0.0')):
        cmd=calls[i]
        assert Path(cmd[4]).name=='四川.py'
        assert cmd[cmd.index('--server.address')+1]==address
        assert cmd[cmd.index('--server.port')+1]=='9080'
        assert '--server.enableXsrfProtection' not in cmd and '--server.enableCORS' not in cmd
        assert cmd[cmd.index('--server.headless')+1]=='true'
    host.os.environ['PORT']='0'
    assert host.main([])==2 and len(calls)==2
