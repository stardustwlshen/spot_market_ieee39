"""Capture six read-only views of the six shipped Dadu market archives with Chromium."""
import argparse
import re
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright


WET = "dadu19_20261127T095231Z_f92c9001a30f"
DRY = "dadu19_20261127T095432Z_cda4af81a8f5"
ERROR = "dadu19_20261127T094442Z_88b5e1e39adf"
CHART = ".vega-embed canvas:visible, canvas.marks:visible, svg.marks:visible"


def choose(page, key, text):
    widget = page.locator(".st-key-" + key)
    box = widget.get_by_role("combobox")
    expect(box).to_be_visible(timeout=90000)
    box.click()
    box.fill(text)
    option = page.get_by_role("option").filter(has_text=text).first
    expect(option).to_be_visible(timeout=12000)
    option.click()


def load_archive(page, run, quarter):
    receipt = page.get_by_text(re.compile(r"当前回放[：:].*" + run))
    if not receipt.count() or not receipt.first.is_visible():
        choose(page, "dadu_saved", run[-6:])
    expect(receipt.first).to_be_visible(timeout=90000)
    name = f"dadu_q_{run}"
    choose(page, name, f"第 {quarter + 1} 段")
    expect(page.locator(".st-key-" + name).get_by_role("combobox")).to_have_value(
        re.compile(rf"第 {quarter + 1} 段$"), timeout=30000)
    page.keyboard.press("Escape")


def tab(page, label):
    button = page.get_by_role("tab", name=label, exact=True)
    button.click()
    expect(button).to_have_attribute("aria-selected", "true")
    identity = button.get_attribute("aria-controls")
    area = (page.locator(f'[id="{identity}"]') if identity else
            page.locator(".st-key-dadu_role_tabs [role=tabpanel]:visible").first)
    expect(area).to_be_visible()
    return area


def painted(element, colored=False):
    # Vega uses canvas or SVG. A non-empty painted element is checked after scrolling.
    probe = """([el, color]) => {
      let r = el.getBoundingClientRect();
      if (r.width < 150 || r.height < 50) return false;
      if (el.tagName.toLowerCase() === 'svg')
        return [...el.querySelectorAll('path,line,rect,circle')].some(p => {
          let b=p.getBoundingClientRect(); return b.width>2 || b.height>2;
        });
      let context = el.getContext('2d');
      if (!context) return false;
      let {width:w,height:h} = el;
      if (w<120 || h<45) return false;
      let d = context.getImageData(0,0,w,h).data, found=0;
      for (let y=1;y<h;y+=2) for(let x=1;x<w;x+=2){
        let k=4*(y*w+x), lo=Math.min(d[k],d[k+1],d[k+2]), hi=Math.max(d[k],d[k+1],d[k+2]);
        if(d[k+3]>40 && (color ? hi-lo>22 : lo<175) && ++found>12) return true;
      }
      return false;
    }"""
    handle = element.element_handle(timeout=30000)
    if handle is None:
        raise RuntimeError("Missing chart or table")
    element.page.wait_for_function(probe, arg=[handle, colored], timeout=30000)


def capture(page, folder, name, anchor, lower, draw=None, colored=False):
    expect(anchor).to_be_visible(timeout=45000)
    expect(lower).to_be_visible(timeout=45000)
    page.mouse.move(1, 1)
    page.evaluate("() => document.fonts.ready")
    # Keep the actual page layout; move its Streamlit scroll container, leaving
    # room for its sticky header. Extend only height when the evidence needs it.
    for _ in range(3):
        anchor.evaluate("""el => {
          el.scrollIntoView({block:'start',behavior:'instant'});
          let host=el.parentElement;
          while(host && !(host.scrollHeight>host.clientHeight+2 &&
                         /auto|scroll/.test(getComputedStyle(host).overflowY))) host=host.parentElement;
          let delta=el.getBoundingClientRect().top-82;
          if(host) host.scrollBy({top:delta,behavior:'instant'});
          else window.scrollBy({top:delta,behavior:'instant'});
        }""")
        page.evaluate("() => new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)))")
        start, end = anchor.bounding_box(), lower.bounding_box()
        if not start or not end:
            raise RuntimeError(f"{name} has no layout bounds")
        needed = max(start["y"]+start["height"], end["y"]+end["height"])+20
        if needed <= page.viewport_size["height"]+1:
            break
        if needed > 1800:
            raise RuntimeError(f"{name} expected one compact evidence section, got {needed:.0f} px")
        page.set_viewport_size({"width":page.viewport_size["width"], "height":int(needed)+8})
    if draw is not None:
        painted(draw, colored)
    expect(page.locator('[data-testid="stException"]:visible')).to_have_count(0)
    expect(page.get_by_role("listbox")).to_have_count(0)
    expect(page.locator('[role="dialog"]:visible')).to_have_count(0)
    expect(page.locator('[data-testid="stSpinner"]:visible')).to_have_count(0, timeout=30000)
    expect(page.get_by_role("button", name="Stop", exact=True)).to_have_count(0, timeout=45000)
    for element in (anchor, lower):
        clear = element.evaluate("""el => {
          const r=el.getBoundingClientRect(), y=r.top+Math.min(15,r.height/2);
          const x=Math.min(innerWidth-3,Math.max(3,r.left+Math.min(20,r.width/2)));
          const hit=document.elementFromPoint(x,y);
          return Boolean(hit && (el.contains(hit) || hit.contains(el) || el.parentElement.contains(hit)) &&
                         r.top>=0 && r.bottom<=innerHeight+2);
        }""")
        if not clear:
            raise RuntimeError(f"{name}: evidence is clipped or covered")
    path = folder / (name + ".png")
    page.screenshot(path=str(path), animations="disabled", caret="hide", timeout=45000)
    if path.stat().st_size < 10000:
        raise RuntimeError(f"{path} unexpectedly small")
    print(f"{path}  {page.viewport_size}", flush=True)


def company(page, folder, mobile):
    load_archive(page, WET, 0)
    area = tab(page, "公司报量报价")
    title = area.get_by_text("公司冻结申报", exact=True).first
    station = area.get_by_text(re.compile(r"瀑布沟.*00:00–00:15.*为何这样报")).first
    expect(station).to_be_visible()
    expect(area.get_by_text("各站提交的报价段", exact=True)).to_be_visible()
    expect(area.get_by_text(re.compile(r"内部计划落在第.*公司冻结申报"))).to_be_visible()
    # The portfolio chart belongs to a closed expander; the frozen Vega step chart is visible.
    chart = area.locator(CHART).first
    capture(page, folder, "company-mobile" if mobile else "company",
            station if mobile else title, chart, chart, True)


def desktop_views(page, folder):
    load_archive(page, DRY, 77)
    area = tab(page, "全流域水情")
    header = area.get_by_role("heading", name="全流域总览")
    expect(area.get_by_text(re.compile(r"19:15–19:30 · 实时执行"))).to_be_visible()
    expect(area.get_by_text("四库期末库存", exact=True)).to_be_visible()
    chart = area.locator(CHART).first
    capture(page, folder, "basin", header, chart, chart, True)
    balance = area.get_by_text("全流域水量平衡", exact=True)
    grid = area.locator('[data-testid="stDataFrame"]:visible').first
    canvas = grid.locator("canvas:visible").first
    capture(page, folder, "water-balance", balance, grid, canvas)


def market_views(page, folder):
    load_archive(page, ERROR, 78)
    plan = tab(page, "日前预出清")
    expect(plan.get_by_text("优化覆盖 96 个时段。当前显示 19:30–19:45 的组合。", exact=True).first).to_be_visible()
    scuc = plan.get_by_text(re.compile(r"日前 SCUC.*煤机开停")).first
    expect(plan.get_by_text(re.compile(r"日前 SCED.*固定煤机组合"))).to_be_visible()
    groups = plan.locator('[data-testid="stDataFrame"]:visible').first
    capture(page, folder, "day-ahead", scuc, groups, groups.locator("canvas:visible").first)
    rt = tab(page, "实时出清与执行")
    roll = rt.get_by_text(re.compile(r"实时滚动 SCED.*每次执行首段")).first
    expect(rt.get_by_text(re.compile(r"^本次预测窗口 19:30–.*执行 19:30–19:45")).first).to_be_visible()
    withheld = rt.get_by_text(re.compile(r"原始实时价.*失负荷.*收入暂停")).first
    expect(withheld).to_be_visible()
    capture(page, folder, "real-time", roll, withheld)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8519")
    parser.add_argument("--out", type=Path, default=Path("evidence"))
    args = parser.parse_args()
    url = urlsplit(args.url)
    if url.hostname not in ("127.0.0.1","localhost","::1") or url.scheme != "http":
        parser.error("Use the loopback Streamlit replay server")
    args.out.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        for width, height, mobile in ((1440, 1024, False), (430, 932, True)):
            context = browser.new_context(viewport={"width":width, "height":height},
                                          device_scale_factor=1, locale="zh-CN",
                                          timezone_id="Asia/Shanghai", reduced_motion="reduce",
                                          is_mobile=mobile, has_touch=mobile)
            page = context.new_page()
            page.set_default_timeout(30000)
            page.goto(args.url, wait_until="domcontentloaded", timeout=90000)
            try:
                company(page, args.out, mobile)
                if not mobile:
                    desktop_views(page, args.out)
                else:
                    market_views(page, args.out)
            except Exception:
                page.screenshot(path=str(args.out/("debug-mobile.png" if mobile else "debug-desktop.png")),
                                animations="disabled", timeout=15000)
                print(page.locator("body").inner_text()[:3000], flush=True)
                raise
            finally:
                context.close()
        browser.close()


if __name__ == "__main__":
    main()
