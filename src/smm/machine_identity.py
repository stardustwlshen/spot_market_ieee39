"""Read-only OS identity evidence, outside the copied/shared project directory.

Hashes avoid saving raw system identifiers. This is local ownership evidence, not
remote attestation: cloned OS/hardware identifiers are outside the trust model.
Missing/unsupported evidence must fail closed; never substitute a hostname.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
import re
import subprocess
import sys
import uuid


def _uuid(value):
    value = str(value).strip().strip('\x00')
    parsed = uuid.UUID(value)
    if parsed.int == 0:
        raise ValueError('empty system identifier')
    return str(parsed)


def _evidence(source, value):
    return {'version': 1, 'source': source,
            'sha256': hashlib.sha256(('ieee39:' + source + ':' + _uuid(value)).encode()).hexdigest()}


def machine_identity():
    """No generated key in the project: copying runs must not copy ownership."""
    try:
        if sys.platform.startswith('linux'):
            return _evidence('linux-machine-id', Path('/etc/machine-id').read_text())
        if sys.platform == 'darwin':
            output = subprocess.check_output(['/usr/sbin/ioreg', '-rd1', '-c', 'IOPlatformExpertDevice'],
                                             text=True, timeout=2)
            value = re.search(r'"IOPlatformUUID"\s*=\s*"([^"]+)"', output)
            return _evidence('macos-platform-uuid', value.group(1)) if value else None
        if sys.platform == 'win32':
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\Microsoft\Cryptography',
                                0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
                return _evidence('windows-machine-guid', winreg.QueryValueEx(key, 'MachineGuid')[0])
    except (OSError, ValueError, AttributeError, ImportError, subprocess.SubprocessError):
        pass
    return None


def boot_identity():
    """Kernel boot session, independent of adjustable wall-clock boot_time.

No Windows fallback to timestamp/registry heuristics: without a verified session
identifier reboot recovery remains unavailable, though same-boot inspection works.
"""
    try:
        if sys.platform.startswith('linux'):
            return _evidence('linux-boot-id', Path('/proc/sys/kernel/random/boot_id').read_text())
        if sys.platform == 'darwin':
            value = subprocess.check_output(['/usr/sbin/sysctl', '-n', 'kern.bootsessionuuid'],
                                            text=True, timeout=2)
            return _evidence('macos-boot-session-uuid', value)
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    return None


def valid_evidence(value):
    return (isinstance(value, dict) and type(value.get('version')) is int and value.get('version') == 1
            and isinstance(value.get('source'), str) and bool(value['source'])
            and isinstance(value.get('sha256'), str)
            and re.fullmatch('[0-9a-f]{64}', value['sha256']) is not None)
