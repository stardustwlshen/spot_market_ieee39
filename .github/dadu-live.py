"""Run from guangdong_market/program with Streamlit serving the actual app."""
import argparse
import gzip
import json
import re
import time
from pathlib import Path

from playwright.sync_api import expect, sync_playwright


MONITOR = r"""() => {
  const rows = [];
  const seen = new Set();
  const started = performance.now();
  const collect = () => {
    for (const node of document.querySelectorAll(
            '[data-testid="stProgress"], [data-testid="stSpinner"], [data-testid="stMarkdownContainer"] p')) {
      if (!node.getClientRects().length || getComputedStyle(node).visibility === 'hidden') continue;
      const text = (node.innerText || '').trim().replace(/\s+/g, ' ');
      if (text.length > 360 || !(/阶段\s*\d+\s*\/\s*6|本项完成\s*\d+\s*\/\s*\d+|正在计算.*(?:second|minute|秒)|结果已保存.*用时/.test(text))) continue;
      if (seen.has(text)) continue;
      seen.add(text);
      const r = node.getBoundingClientRect();
      rows.push({ms: Math.round(performance.now()-started), text, in_viewport: r.bottom>0 && r.top<innerHeight});
    }
  };
  const observer = new MutationObserver(collect);
  observer.observe(document.body, {subtree:true, childList:true, attributes:true, characterData:true});
  window.daduAudit = {rows, observer};
}"""

RUN_RE = re.compile(r'dadu19_\d{8}T\d{6}Z_[a-f0-9]{12}')
ROLE_TABS = ('公司报量报价', '日前预出清', '实时出清与执行', '全流域水情', '收入与对照')


def select_entry(page, label, prefix):
    box = page.get_by_role('combobox', name=label, exact=True)
    box.wait_for()
    if box.input_value().startswith(prefix):
        return
    box.click()
    box.fill(prefix)
    opt = page.get_by_role('option').filter(has_text=prefix).first
    if not opt.is_visible():
        box.press('ArrowDown')
    opt.click(timeout=15000)
    expect(box).to_have_value(re.compile(re.escape(prefix)), timeout=20000)


def get_run(page):
    caption = page.get_by_text(re.compile(r'^当前回放：')).first
    if not caption.is_visible():
        return None, ''
    text = caption.inner_text()
    m = RUN_RE.search(text)
    return (m.group(0) if m else None), text


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--url', default='http://127.0.0.1:8519')
    ap.add_argument('--output', type=Path, default=Path('evidence'))
    ap.add_argument('--archive-root', type=Path, default=Path('runs_sichuan_v5_consultation/dadu19'))
    args = ap.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    log = {'url': args.url, 'run_clicks': 0}
    logfile = args.output/'progress.json'
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={'width': 1440, 'height': 990}, locale='zh-CN')
        page.set_default_timeout(20000)
        try:
            page.goto(args.url, wait_until='domcontentloaded', timeout=60000)
            expect(page.get_by_text('大渡河梯级与四川 V5.0 现货市场',exact=True)).to_be_visible(timeout=90000)
            header = page.locator('summary').filter(has_text='运行设置与新算例').first
            header.wait_for()
            if not header.evaluate("e=>e.closest('details').open"):
                header.click()
            log['prior_run'] = get_run(page)[0]
            select_entry(page, '市场场景', '丰水低负荷')
            select_entry(page, '公司报价策略', '水价值报价')
            select_entry(page, '实时执行范围', '首小时')
            # Confirm that the server accepted the selection, independent of the editable combobox text.
            draft = page.get_by_text(re.compile(r'本次运行设置.*日前\s*96\s*段\s*/\s*实时\s*4\s*段')).first
            expect(draft).to_be_visible(timeout=25000)
            log['draft'] = draft.inner_text()
            page.evaluate(MONITOR)
            log['run_clicks'] += 1
            page.get_by_role('button',name='生成公司报价并运行市场',exact=True).click()
            counter = re.compile(r'本项完成\s*(\d+)\s*/\s*(\d+)\s*(座电站|项校核)')
            timeout_at = time.monotonic() + 620
            screenshot_done = False
            while time.monotonic() < timeout_at:
                failure = page.locator('[data-testid="stException"]')
                if failure.count() and failure.first.is_visible():
                    raise AssertionError(failure.first.inner_text())
                visible_counter = page.get_by_text(counter).first
                if not screenshot_done and visible_counter.is_visible():
                    s = visible_counter.inner_text()
                    match = counter.search(s)
                    stage = page.get_by_text(re.compile(r'阶段\s*[23]\s*/\s*6')).first
                    if match and stage.is_visible() and 0 < int(match[1]) < int(match[2]):
                        stage.scroll_into_view_if_needed()
                        page.screenshot(path=str(args.output/'progress.png'), animations='disabled')
                        log['screenshot_state'] = {'stage': stage.inner_text(), 'counter': s}
                        screenshot_done = True
                rid, caption = get_run(page)
                if rid and rid != log['prior_run'] and re.search(r'4\s*/\s*96\s*段', caption):
                    log.update(run=rid, caption=caption)
                    break
                page.wait_for_timeout(180)
            else:
                raise AssertionError('No new executed 4/96 archive in the browser before timeout')
            log['events'] = page.evaluate('() => window.daduAudit.rows')
            assert screenshot_done and len({row['text'] for row in log['events'] if counter.search(row['text'])}) >= 2
            phases = sorted({int(m[1]) for row in log['events'] if (m:=re.search(r'阶段\s*(\d+)\s*/\s*6', row['text']))})
            assert len(phases) >= 3 and 5 in phases, phases
            log['observed_phases'] = phases
            path = args.archive_root/log['run']
            receipt = json.loads((path/'receipt.json').read_bytes())
            data = json.loads(gzip.decompress((path/'result.json.gz').read_bytes()))
            assert receipt['periods']==4 and data['run']==log['run']
            assert [x['quarter'] for x in data['da']] == list(range(96))
            assert [x['quarter'] for x in data['rt']] == list(range(4))
            log['archive'] = dict(da=96, rt=4, sha256=receipt['sha256'])
            import hashlib
            assert hashlib.sha256((path/'result.json.gz').read_bytes()).hexdigest()==receipt['sha256']
            for tab in ROLE_TABS:
                t = page.get_by_role('tab',name=tab,exact=True)
                t.click()
                expect(t).to_have_attribute('aria-selected','true')
                pane = page.locator('[id="'+t.get_attribute('aria-controls')+'"]')
                expect(pane).to_be_visible()
                assert len(pane.inner_text().strip())>20
                if tab in ('日前预出清','实时出清与执行'):
                    t.evaluate("e=>{let m=document.querySelector('[data-testid=stMain]');m?.scrollBy({top:e.getBoundingClientRect().top-100,behavior:'instant'})}")
                    page.screenshot(path=str(args.output/('day-ahead.png' if tab=='日前预出清' else 'real-time.png')), animations='disabled')
            log['tabs'] = list(ROLE_TABS)
            log['passed'] = True
        except BaseException as exc:
            log['error'] = repr(exc)
            if not page.is_closed():
                log['events'] = page.evaluate('() => window.daduAudit?.rows || []')
                page.screenshot(path=str(args.output/'failure.png'))
                log['last_visible'] = page.locator('body').inner_text()[:3000]
            raise
        finally:
            logfile.write_text(json.dumps(log,ensure_ascii=False,indent=2),encoding='utf8')
            browser.close()
    print(json.dumps({k:v for k,v in log.items() if k!='events'},ensure_ascii=False))


if __name__=='__main__':
    main()
