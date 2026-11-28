"""Read a published simulation for a first lesson. Opening the lesson never runs a solver."""
import base64
import gzip
import json
from pathlib import Path

from sichuan_v5_dadu_da import read_day

ASSETS = Path(__file__).with_name('dadu_sheet')


def packed_lesson(root, name, label):
    # Validate the published hash and schema before passing data to the browser.
    result = read_day(root, name)
    data = json.dumps(result, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()
    packed = gzip.compress(data, compresslevel=3, mtime=0)
    return {'label': label, 'packed': base64.b64encode(packed).decode('ascii')}


def lesson_html(cases):
    if not cases:
        raise ValueError('入门练习需要至少一份已出清的日前算例')
    css = (ASSETS / 'day_lesson.css').read_text(encoding='utf-8')
    js = (ASSETS / 'day_lesson.js').read_text(encoding='utf-8')
    data = json.dumps(cases, ensure_ascii=False, separators=(',', ':')).replace('<', '\\u003c')
    return ('<!doctype html><html lang="zh-CN"><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1"><title>大渡河 · 第一堂日前现货课</title>'
            '<style>body{margin:0;padding:clamp(6px,2vw,25px);background:#f2f4ee}main{max-width:1160px;margin:auto}'
            + css + '</style><main class="day-lesson"></main><script id="lesson-cases" type="application/json">'
            + data + '</script><script>' + js + '\nrenderDayLesson(document.querySelector("main"),'
            'JSON.parse(document.querySelector("#lesson-cases").textContent));</script></html>')


def render_lesson(case, key):
    import streamlit as st
    component = st.components.v2.component('dadu_first_lesson', html='<div class="day-lesson"></div>',
        css=(ASSETS / 'day_lesson.css').read_text(encoding='utf-8'),
        js=(ASSETS / 'day_lesson.js').read_text(encoding='utf-8') +
           '\nexport default function({parentElement,data}){return void renderDayLesson(parentElement.querySelector(".day-lesson"),[data]);}')
    component(data=case, key=key, width='stretch')
