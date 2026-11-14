"""Explicit accepted DA selection, independent of result-directory text boxes."""
def selection(status):
 if not status.get('accepted') or not status.get('run'):
  raise ValueError('已接受日前结果缺失')
 return {'run':status['run'],'accepted':status['accepted']}
def verify_selection(selected,reader=None):
 if reader is None:
  from gd_da_only import status
  reader=status
 current=reader(selected['run'])
 if not current.get('accepted') or current['accepted']!=selected['accepted'] or current['run']!=selected['run']:
  raise ValueError('日前接受记录已改变，请重新选择')
 return current['run']
