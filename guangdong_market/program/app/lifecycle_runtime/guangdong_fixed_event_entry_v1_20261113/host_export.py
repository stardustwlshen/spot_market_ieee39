"""Portable v6-only export fallback; never imports unregistered producer paths."""
def export(*a,**kw):
 raise ValueError('portable host requires V6Session.export_generated_pair; legacy fixture export unsupported')
