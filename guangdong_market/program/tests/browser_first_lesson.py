"""Run explicitly with Playwright Chromium installed; exercise the exported classroom and the live UI."""
import argparse
from pathlib import Path

from playwright.sync_api import expect, sync_playwright


def check(url, offline):
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={'width':1180,'height':850})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(url)
        root = page.locator('.day-lesson')
        expect(root.get_by_role('heading',name='明天这座水电站要发多少电？')).to_be_visible(timeout=60000)
        if offline:
            selector = root.locator('[data-lesson="case"]')
            for idx in ('1','2','3','0'):
                selector.select_option(idx)
                expect(root).to_have_attribute('aria-busy','false')
        root.get_by_text('展开看电厂怎样分档报价').click()
        expect(root.locator('tbody tr')).to_have_count(10)
        form = root.locator('form')
        form.locator('input').fill('1648.97')
        form.get_by_text('检查答案').click()
        expect(form.locator('output')).to_contain_text('这个数是功率')
        form.locator('input').fill('412.2')
        form.get_by_text('检查答案').click()
        expect(form.locator('output')).to_contain_text('算对了')
        root.get_by_text('还不能',exact=True).click()
        expect(root.locator('[data-price-answer]')).to_contain_text('答对了')
        root.get_by_role('button',name='跟水去深溪沟 19:15').click()
        expect(root.locator('[data-lesson="quarter"]')).to_have_value('77')
        expect(root.locator('[data-lesson="station"]')).to_have_value('D11')
        expect(root.locator('.lesson-body')).to_contain_text('径流式近似')
        root.get_by_role('button',name='追溯到瀑布沟 18:00').click()
        expect(root.locator('[data-lesson="quarter"]')).to_have_value('72')
        root.locator('[data-lesson="quarter"]').select_option('95')
        expect(root.locator('button[data-river="D11"]')).to_have_count(0)
        page.set_viewport_size({'width':390,'height':844})
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        assert not errors, errors
        browser.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('html',type=Path)
    parser.add_argument('--live',required=True)
    args = parser.parse_args()
    check(args.html.resolve().as_uri(),True)
    check(args.live,False)
