from pathlib import Path
import re
from playwright.sync_api import sync_playwright, expect

expect.set_options(timeout=45_000)

with sync_playwright() as pw:
    browser = pw.chromium.launch(channel="chrome", args=["--no-sandbox"])
    for size in ({"width":1440,"height":940}, {"width":390,"height":844}):
        page = browser.new_page(viewport=size)
        page.set_default_timeout(60_000)
        page.on("pageerror",lambda error:print("Chrome page error",error,flush=True))
        print("Starting",size,flush=True)
        page.goto("http://127.0.0.1:8519")
        for tab in ("公司报价","市场中标","实时实发","梯级水量","收入与对照"):
            expect(page.get_by_role("tab", name=tab, exact=True)).to_have_count(1)
        print("Initial tabs",page.get_by_role("tab").all_text_contents(),flush=True)
        def select_tab(name):
            group = page.get_by_role("tablist").filter(has=page.get_by_role("tab",name="公司报价",exact=True))
            item = group.get_by_role("tab",name=name,exact=True)
            print("Opening tab",name,"matching DOM elements",item.count(),flush=True)
            expect(item).to_have_count(1)
            item.click()
            expect(item).to_have_attribute("aria-selected","true")
        def choose(key, text):
            shell = page.locator(".st-key-"+key)
            print('Selecting',key,text,flush=True)
            shell.get_by_role("combobox").click()
            page.get_by_role("option").filter(has_text=text).last.click()
        def selected(key, value):
            selector = ".st-key-"+key
            try:
                page.wait_for_function("""([selector, needle]) => {
                    const widget = document.querySelector(selector);
                    if (!widget) return false;
                    return [widget.innerText,...Array.from(widget.querySelectorAll('input,[role="combobox"]'), n=>
                        [n.value,n.title,n.getAttribute('aria-valuetext'),n.getAttribute('placeholder')].join(' '))].join(' ').includes(needle);
                }""", arg=[selector,value], timeout=45_000)
            except Exception:
                print("Unreadable selection",key,page.locator(selector).first.evaluate("(e)=>e.outerHTML.slice(0,4500)"),flush=True)
                print("VISIBLE ERRORS", page.locator('[data-testid="stException"]').all_text_contents(),flush=True)
                raise
        choose("dadu_saved", "e39adf")
        page.get_by_text(re.compile("当前回放：预测偏差 · 朴素报价")).wait_for()
        folder = Path("evidence")
        folder.mkdir(exist_ok=True)
        page.get_by_role("button",name="跳到最大失负荷时段").click()
        selected("dadu_q_dadu19_20261127T094442Z_88b5e1e39adf","19:15–19:30")
        select_tab("梯级水量")
        reach = page.get_by_role("tab", name="河段时滞与在途",exact=True)
        expect(reach).to_have_count(1)
        reach.click()
        handoff = page.get_by_role("button",name="查看深溪沟到水时段")
        expect(handoff).to_be_visible()
        handoff.scroll_into_view_if_needed()
        page.screenshot(path=str(folder/f"river-{size['width']}.png"))
        handoff.click()
        selected("dadu_unit_dadu19_20261127T094442Z_88b5e1e39adf","深溪沟")
        selected("dadu_q_dadu19_20261127T094442Z_88b5e1e39adf","20:30–20:45")
        print("WATER PASS",size["width"],"D10 period 78 → D11 period 83",flush=True)
        choose("dadu_unit_dadu19_20261127T094442Z_88b5e1e39adf","安谷")
        selected("dadu_unit_dadu19_20261127T094442Z_88b5e1e39adf","安谷")
        page.get_by_role("button",name="跳到最大失负荷时段").click()
        selected("dadu_q_dadu19_20261127T094442Z_88b5e1e39adf","19:15–19:30")
        for tab in ("公司报价","市场中标","实时实发","梯级水量","收入与对照"):
            select_tab(tab)
            if tab == "收入与对照":
                subtotal = page.get_by_text(re.compile(r"12,135,083\.84"))
                expect(subtotal).to_be_visible()
                subtotal.scroll_into_view_if_needed()
            page.screenshot(path=str(folder/(str(("公司报价","市场中标","实时实发","梯级水量","收入与对照").index(tab)+1)+f"-{size['width']}.png")))
            expect(page.locator('[data-testid="stException"]')).to_have_count(0)
        page.get_by_role("button",name="回放另一策略的同站同时段").click()
        page.get_by_text(re.compile("当前回放：预测偏差 · 水价值报价")).wait_for()
        selected("dadu_unit_dadu19_20261127T094442Z_9a78ae68318c","安谷")
        selected("dadu_q_dadu19_20261127T094442Z_9a78ae68318c","19:15–19:30")
        expect(page.locator('[data-testid="stException"]')).to_have_count(0)
        select_tab("实时实发")
        folder = Path("evidence")
        folder.mkdir(exist_ok=True)
        page.screenshot(path=str(folder/f"dispatch-{size['width']}.png"))
        if size["width"]==390:
            boxes = page.locator(".st-key-dadu_overview_metrics [data-testid=stMetric]").evaluate_all("(ns)=>ns.map(n=>[n.offsetLeft,n.getBoundingClientRect().top])")
            print("MOBILE KPI",boxes,flush=True)
            assert len(boxes)==4 and abs(boxes[0][1]-boxes[1][1])<6
        print("CHROME PASS", size["width"], "5 role tabs, naive → water_value, 安谷, 第78段",flush=True)
        page.close()
    browser.close()
