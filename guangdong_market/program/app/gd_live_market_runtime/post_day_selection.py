from source import e
def check(session,selected,*,enabled=False,windows=4,reference_revision=False,post_scuc_planning=False,post_declarations=None):
 if not enabled:return None
 e.need(windows==96 and not reference_revision and not post_scuc_planning and not post_declarations,'post96 independent mode required')
 from gd_post_scuc_runtime.day_calendar import expected
 rows,pins=expected(session.run,selected['declaration_path'])
 e.need(set(rows)==set(range(1,25)),'all24 declarations required')
 return dict(hours=list(rows),source_pins=pins,planning_not_award=True,H25_authorized=False,reference_available_after='accepted W94 / cutoff285',native_calls=0)
