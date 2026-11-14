"""Explicit finite H1 legacy or source-bound hourly research planning. Not qualification certification."""
def validate(*,enabled=False,hourly=False,windows=4,post_scuc_planning=False,post_scuc_day=False,reference_revision=False,candidate_loss_once=False):
 if type(enabled)is not bool or type(hourly)is not bool:raise ValueError('explicit finite mode booleans')
 if enabled and hourly:raise ValueError('choose finite H1 or hourly')
 if enabled and not(windows==4 and post_scuc_planning and not post_scuc_day and not reference_revision and not candidate_loss_once):raise ValueError('finite H1 requires original four-window planning mode')
 if hourly and not(windows==96 and post_scuc_day and not post_scuc_planning and not reference_revision and not candidate_loss_once):raise ValueError('finite hourly requires complete post96 calendar; no classic reference or loss mode')
def preflight(session,**kw):
 validate(**kw)
 if not(kw.get('enabled')or kw.get('hourly')):return {'enabled':False}
 session.verify_sources()
 if session.registry['case_identity']!='c10e5cbfbe0f39f028d10cb2b2d30003e803033a3d883f4c753339bc9c1cdbe9':raise ValueError('current hourly integration supports original c10e/50/400 only; new inventory requires explicit source adapter')
 hourly=kw.get('hourly',False)
 return dict(enabled=True,hours=list(range(1,25))if hourly else[1],maximum_native_calls=289 if hourly else 12,scope='finite choice for each due declared hour; original thermal qualification inventory',future_forecast_is_not_award=True,H25_authorized=False,formal=False,publication_allowed=False)
