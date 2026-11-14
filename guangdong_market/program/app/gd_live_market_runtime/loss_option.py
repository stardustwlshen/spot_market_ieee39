"""Early scope rejection only; not acceptance or correction authority."""
def preflight(session,*,enabled=False,ac_once=False):
 if not enabled:return {'enabled':False,'extra_SCED':0,'extra_AC':0}
 if not ac_once:raise ValueError('loss correction requires explicit initial AC')
 session.verify_sources()
 if session.registry['case_identity']!='c10e5cbfbe0f39f028d10cb2b2d30003e803033a3d883f4c753339bc9c1cdbe9':raise ValueError('loss correction currently supports original c10e case only; edited cases are not certified for this helper')
 return {'enabled':True,'extra_SCED':1,'extra_AC':1,'scope':'original c10e; actual candidate must still verify AGC50/reserve400/zero prior loss at callback','no_retry':True,'safety_release':False}
