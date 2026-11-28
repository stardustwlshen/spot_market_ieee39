"""Run explicitly with Playwright Chromium installed; exercise the exported classroom and the live UI."""
import argparse
from pathlib import Path

from playwright.sync_api import expect, sync_playwright


def check_classroom(url):
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={'width':1180,'height':850})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(url)
        root = page.locator('.day-lesson')
        expect(root.get_by_role('heading',name='明天这座水电站要发多少电？')).to_be_visible(timeout=60000)
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


def check_workstation(url):
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={'width': 1280, 'height': 850})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(url + '?stage=learn&scenario=wet_normal')
        root = page.locator('.dispatch-console')
        expect(root.get_by_role('heading', name='运行总览')).to_be_visible(timeout=60000)
        fleet = root.locator('.dc-ledger tbody')
        day = root.get_by_role('region', name='全日系统统计')
        expect(fleet.locator('tr')).to_have_count(19)
        expect(day.locator('tbody tr')).to_have_count(5)
        expect(day).to_contain_text('丰水低负荷')
        before = day.inner_text()
        clock = root.get_by_label('计划时段', exact=True)
        clock.select_option('95')
        expect(root.get_by_role('button', name='下一时段')).to_be_disabled()
        root.get_by_role('button', name='上一时段').click()
        expect(clock).to_have_value('94')
        expect(root.locator('[data-check-time]')).to_have_text('23:30–23:45')
        root.get_by_label('筛选调节方式').select_option('reservoir')
        expect(fleet.locator('tr')).to_have_count(4)
        expect(fleet).to_contain_text('瀑布沟')
        root.get_by_label('筛选调节方式').select_option('river')
        expect(fleet.locator('tr')).to_have_count(15)
        expect(fleet).to_contain_text('深溪沟')
        assert day.inner_text() == before
        for width in (1280, 390):
            page.set_viewport_size({'width': width, 'height': 844})
            chart = root.locator('.supply-chart')
            chart.click(position={'x': round(chart.bounding_box()['width']*0.50), 'y': 70})
            expect(clock).to_have_value('46')
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        page.get_by_text('机组申报', exact=True).click()
        expect(page.get_by_role('button', name='确认并冻结 19 站申报')).to_be_visible(timeout=30000)
        assert not errors, errors
        browser.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('html',type=Path)
    parser.add_argument('--live',required=True)
    args = parser.parse_args()
    check_classroom(args.html.resolve().as_uri())
    check_workstation(args.live)
