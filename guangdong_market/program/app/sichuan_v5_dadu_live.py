"""Display progress reported by the actual Dadu solver."""
from time import monotonic

import streamlit as st


# Backend counters count different units. Stage order is shared by 4- and 96-period runs.
_STAGES = {
    'inputs': (1, '载入事前数据', '项输入'),
    'company': (2, '公司报量报价', '座电站'),
    'market': (3, '核定市场边界', '项校核'),
    'da': (4, '日前预出清', '项求解'),
    'rt': (5, '实时出清与执行', '个时段'),
    'archive': (6, '结算与归档', '项'),
}


class LiveProgress:
    def __init__(self, container, label):
        self.label = label
        self.started = monotonic()
        self.last_message = None
        self.last_stage = ''
        self.stage = container.empty()
        self.bar = container.empty()
        self.stage.markdown(f'**{label}** · 正在准备输入')

    def __call__(self, event):
        stage = getattr(event, 'stage', None)
        message = getattr(event, 'label', str(event))
        done, total = getattr(event, 'completed', None), getattr(event, 'total', None)
        marker = (stage, message, done, total)
        if self.last_message == marker:
            return
        self.last_message = marker
        phase = _STAGES.get(stage)
        self.last_stage = f'阶段 {phase[0]}/{len(_STAGES)} · {phase[1]}' if phase else ''
        heading = ' · '.join(filter(None, (self.label, self.last_stage)))
        self.stage.markdown(f'**{heading}** · {message}')
        if type(done) is int and type(total) is int and 0 <= done <= total and total > 0:
            unit = phase[2] if phase else '项'
            self.bar.progress(done / total, text=f'本项完成 {done} / {total} {unit}')
        else:
            self.bar.empty()

    def finish(self):
        self.bar.empty()
        self.stage.success(f'{self.label}完成，结果已保存 · 用时 {monotonic()-self.started:.1f} 秒')

    def fail(self, error):
        self.bar.empty()
        last = self.last_message[1] if self.last_message else '准备输入'
        where = ' · '.join(filter(None, (self.last_stage, last)))
        self.stage.error(f'{self.label}中断于「{where}」 · 用时 {monotonic()-self.started:.1f} 秒。{error}')


def solve_visible(run, root, settings, container, label):
    display = LiveProgress(container, label)
    with container:
        try:
            with st.spinner('正在计算 · 总用时', show_time=True):
                result = run(root, **settings, progress=display)
        except (ValueError, RuntimeError, OSError) as error:
            display.fail(error)
            raise
    display.finish()
    return result
