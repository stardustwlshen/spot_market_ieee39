"""Read-only scope guard for the single-day result catalog and links."""
from pathlib import Path


def is_single_day_result(meta):
    """Exclude sequential-study records by provenance, never by their title.

    A copied day retains its commit marker. The parent identity also detects
    an uncommitted study day. No archive is edited, relabelled or deleted.
    """
    if meta.get('series_root') or meta.get('study_root'):
        return False
    directory = meta.get('dir')
    if not directory:
        return False
    path = Path(directory)
    return not ((path / 'day_commit.json').exists()
                or (path.parent / 'series_identity.json').exists())
