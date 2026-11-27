from pathlib import Path
import re
from playwright.sync_api import sync_playwright, expect

with sync_playwright() as pw:
    browser = pw.chromium.launch(channel="chrome", args=["--no-sandbox"])
    for size in ({"width":1440,"height":940}, {"width":390,"height":844}):
        page = browser.new_page(viewport=size)
        page.set_default_timeout(60_000)
        page.goto("http://127.0.0.1:8519")
        for tab in ("公司报价","市场中标","实时实发","梯级水量","收入与对照"):
            expect(page.get_by_role("tab", name=tab, exact=True)).to_have_count(1)
        def choose(key, text):
            shell = page.locator(".st-key-"+key)
            shell.get_by_role("combobox").click()
            page.get_by_role("option").filter(has_text=text).last.click()
        choose("dadu_saved", "e39adf")
        page.get_by_text(re.compile("当前回放：预测偏差 · 朴素报价")).wait_for()
        choose("dadu_unit_dadu19_20261127T094442Z_88b5e1e39adf","安谷")
        page.get_by_role("button",name="跳到最大失负荷时段").click()
        expect(page.locator(".st-key-dadu_q_dadu19_20261127T094442Z_88b5e1e39adf")).to_contain_text("19:15–19:30")
        for tab in ("公司报价","市场中标","实时实发","梯级水量","收入与对照"):
            button = page.get_by_role("tab", name=tab, exact=True)
            button.click()
            expect(button).to_have_attribute("aria-selected","true")
            expect(page.locator('[data-testid="stException"]')).to_have_count(0)
        page.get_by_role("button",name="回放另一策略的同站同时段").click()
        page.get_by_text(re.compile("当前回放：预测偏差 · 水价值报价")).wait_for()
        expect(page.locator(".st-key-dadu_unit_dadu19_20261127T094442Z_9a78ae68318c")).to_contain_text("安谷")
        expect(page.locator(".st-key-dadu_q_dadu19_20261127T094442Z_9a78ae68318c")).to_contain_text("19:15–19:30")
        expect(page.locator('[data-testid="stException"]')).to_have_count(0)
        page.get_by_role("tab",name="实时实发",exact=True).click()
        folder = Path("evidence")
        folder.mkdir(exist_ok=True)
        page.screenshot(path=str(folder/f"dispatch-{size['width']}.png"))
        print("CHROME PASS", size["width"], "5 role tabs, naive → water_value, 安谷, 第78段",flush=True)
        page.close()
    browser.close()
