"""Explicit provincial fifteen-minute grid; horizon length is a research choice."""
def supported(mode='provincial8'):
 if mode!='provincial8':raise ValueError('省级候选仅支持15分钟网格；旧5分钟模式不能混用')
 return dict(dispatch_mode='provincial8',dispatch_scope='8 x 15-minute rolling horizon; two-hour horizon is a research assumption',grid_minutes=15,horizon_periods=8,staged_24_24_enabled=False)
