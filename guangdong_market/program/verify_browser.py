from pathlib import Path
from shutil import which
from playwright.sync_api import sync_playwright,expect
import subprocess,time,sys,urllib.request
root = Path.cwd()
app = subprocess.Popen([sys.executable,'scripts/launch_sichuan.py','--port','8509'])
try:
    for _ in range(100):
        try:
            if urllib.request.urlopen('http://127.0.0.1:8509/_stcore/health',timeout=1).status==200:
                break
        except OSError:
            time.sleep(.5)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=which('google-chrome'),headless=True,args=['--no-sandbox'])
        page = browser.new_page(viewport={'width':1440,'height':1050})
        page.set_default_timeout(45000)
        page.goto('http://127.0.0.1:8509/?scenario=wet_normal&stage=filing')
        expect(page.get_by_text('次日工况与特殊运行要求',exact=False).first).to_be_visible()
        expect(page.get_by_text('选择要撤回的例外')).to_be_visible()
        expect(page.get_by_role('button',name='运行日前预出清')).to_be_enabled()
        assert page.locator('[data-testid="stException"]').count()==0
        page.screenshot(path='qa-results/01-filing.png')
        page.goto('http://127.0.0.1:8509/?scenario=wet_normal&stage=da')
        expect(page.get_by_role('heading',name='日前预出清结果')).to_be_visible()
        expect(page.get_by_text('孤网供电',exact=False).first).to_be_visible()
        assert page.locator('[data-testid="stException"]').count()==0
        page.screenshot(path='qa-results/02-dayahead.png')
        browser.close()
finally:
    app.terminate()
    app.wait(timeout=15)
