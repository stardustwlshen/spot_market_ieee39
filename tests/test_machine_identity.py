"""Read-only OS sources; missing or malformed IDs never become a shared default."""
from types import SimpleNamespace
import sys

import pytest
from smm import machine_identity as mi

UUID = 'a1234567-1234-1234-1234-123456789abc'


def test_linux_reads_system_not_project_identity(monkeypatch):
    monkeypatch.setattr(mi, 'sys', SimpleNamespace(platform='linux'))
    calls = []
    def read(path, *args, **kw):
        # Preserve the exact system paths while ignoring host path separators.
        calls.append(path.as_posix())
        return UUID
    monkeypatch.setattr(mi.Path, 'read_text', read)
    machine, boot = mi.machine_identity(), mi.boot_identity()
    assert calls == ['/etc/machine-id', '/proc/sys/kernel/random/boot_id']
    assert mi.valid_evidence(machine) and mi.valid_evidence(boot)
    assert machine['sha256'] != boot['sha256']
    assert UUID not in str(machine)


@pytest.mark.parametrize('value', ['', 'unknown', '0'*32])
def test_bad_identity_fails_closed(monkeypatch, value):
    monkeypatch.setattr(mi, 'sys', SimpleNamespace(platform='linux'))
    monkeypatch.setattr(mi.Path, 'read_text', lambda *a, **kw: value)
    assert mi.machine_identity() is None and mi.boot_identity() is None


def test_macos_native_sources_and_no_procfs(monkeypatch):
    monkeypatch.setattr(mi, 'sys', SimpleNamespace(platform='darwin'))
    monkeypatch.setattr(mi.Path, 'read_text', lambda *a, **kw: pytest.fail('unexpected filesystem read'))
    def output(cmd, **kw):
        if cmd[0] == '/usr/sbin/ioreg':return '"IOPlatformUUID" = "' + UUID + '"'
        assert cmd == ['/usr/sbin/sysctl', '-n', 'kern.bootsessionuuid']
        return UUID
    monkeypatch.setattr(mi.subprocess, 'check_output', output)
    assert mi.machine_identity()['source'] == 'macos-platform-uuid'
    assert mi.boot_identity()['source'] == 'macos-boot-session-uuid'


def test_windows_native_registry_and_reboot_evidence_unavailable(monkeypatch):
    from contextlib import nullcontext
    monkeypatch.setattr(mi, 'sys', SimpleNamespace(platform='win32'))
    registry = SimpleNamespace(HKEY_LOCAL_MACHINE=1, KEY_READ=2, KEY_WOW64_64KEY=4,
                               OpenKey=lambda *a: nullcontext('key'), QueryValueEx=lambda *a: (UUID, 1))
    monkeypatch.setitem(sys.modules, 'winreg', registry)
    assert mi.machine_identity()['source'] == 'windows-machine-guid'
    assert mi.boot_identity() is None


def test_source_permission_denial_is_unknown(monkeypatch):
    monkeypatch.setattr(mi, 'sys', SimpleNamespace(platform='linux'))
    def denied(*a, **kw):raise PermissionError('restricted')
    monkeypatch.setattr(mi.Path, 'read_text', denied)
    assert mi.machine_identity() is None and mi.boot_identity() is None
