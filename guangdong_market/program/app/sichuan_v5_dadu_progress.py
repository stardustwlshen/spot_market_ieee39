"""Read-only progress reports for Dadu experiments and audited archive compatibility."""


class ProgressEvent(str):
    """A displayable string for existing callbacks, with completed work for a UI."""

    def __new__(cls, stage, completed, total, label):
        value = super().__new__(cls, f"{label}（{completed}/{total}）")
        value.stage = stage
        value.completed = completed
        value.total = total
        value.label = label
        return value


def report(sink, stage, completed, total, label):
    if sink is not None:
        sink(ProgressEvent(stage, completed, total, label))


# Exact telemetry and citation-only revisions. Receipts retain the raw
# source hashes. Any other change to code or data requires fresh comparison runs.
_PROGRESS_REVISIONS = {
    "app/sichuan_v5_dadu_case.py": {
        "e4923cdf18cebfc8a65a1575cf5307aa139870da9242af7610746a41ecddd887",
        "07f8394f6d9a0e6a6d2bdde2bebc66dcb888381a9d5a96ad966ebdd50641a488",
    },
    "app/sichuan_v5_company.py": {
        "20ce4656e4cf1bc87b1a3b0ec2facc7398687564d00c9708e7d8d2bfa1ec7423",
        "010bddcc13729aacba0c40c349c5277fac2c4fee3a618cd2fde76020d1d89e89",
    },
    "app/sichuan_v5_dadu_policy.py": {
        "2880c64e2750c7c79f479f51737c0801f4b640613c16ee6c2426b1c1310b3641",
        "89ea251499f97f490d6d20ede3bd330b7f416f4e93ab0d1390f341e45e6a2977",
    },
    "app/sichuan_v5_dadu_run.py": {
        "d4b32b15a1968457a548330225f5570a65309e8b3cc2eda2ac54f954a1b1ed6c",
        "f1daa2ca355c22c266bf7efabb8348060f9813058cd4c38f2c26f78f0caabdd8",
    },
    "app/sichuan_v5_energy.py": {
        "cc5f29b4275a0411fb039e18f70e323df4f2dcd75350c20f85a2bec7dc0fe3e2",
        "8502f9b2039787f3d6e18d884de90634e3ce282365444a789fc11d37944d62c7",
    },
}


def source_hashes_match(left, right):
    """Allow exact manifests and the audited telemetry or document-citation source revisions."""
    if not isinstance(left, dict) or not isinstance(right, dict) or not left or left.keys() != right.keys():
        return False
    for path, a in left.items():
        b = right[path]
        if not isinstance(a, str) or not a or not isinstance(b, str) or not b:
            return False
        if a != b:
            revisions = _PROGRESS_REVISIONS.get(path)
            if revisions is None or a not in revisions or b not in revisions:
                return False
    return True
