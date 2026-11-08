"""Windows evidence stays unavailable until boot-session semantics are proven."""
import ctypes
import sys
import uuid
from unittest.mock import Mock

import pytest
from smm import machine_identity as identity


def test_windows_does_not_use_unverified_sources(monkeypatch):
    monkeypatch.setattr(identity.sys, 'platform', 'win32')
    forbidden = Mock(side_effect=AssertionError('unverified evidence source used'))
    monkeypatch.setattr(identity.subprocess, 'check_output', forbidden)
    monkeypatch.setattr(identity.Path, 'read_text', forbidden)
    monkeypatch.setattr(ctypes, 'WinDLL', forbidden, raising=False)
    assert identity.boot_identity() is None
    forbidden.assert_not_called()


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows native read-only probe')
def test_native_boot_environment_same_session_only():
    """A stable response is NOT proof of per-reboot uniqueness."""
    try:
        query = ctypes.WinDLL('ntdll').NtQuerySystemInformation
        query.argtypes = [ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32,
                          ctypes.POINTER(ctypes.c_uint32)]
        query.restype = ctypes.c_int32
        samples = []
        for _ in range(3):
            buffer = ctypes.create_string_buffer(32)
            length = ctypes.c_uint32()
            status = query(90, buffer, 32, ctypes.byref(length))
            if status != 0 or length.value != 32:
                pytest.skip('Native API unsupported or unexpected schema')
            value = uuid.UUID(bytes_le=buffer.raw[:16])
            if value.int == 0:
                pytest.skip('Native API provided no identifier')
            samples.append(value)
    except (OSError, AttributeError):
        pytest.skip('Native API unavailable')
    assert len(set(samples)) == 1
    assert identity.boot_identity() is None
