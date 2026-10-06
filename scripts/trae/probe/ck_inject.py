# -*- coding: utf-8 -*-
"""用完整 CK（含 X-Cloudide-Session）注入隔离浏览器，看能不能进 Trae 网页版。

原版是 trae-recon/trae-authz-ck.mjs（Node），这里改成 Python 是因为
playwright 只装在 wb-manager 的 venv 里，trae-recon 没有 node_modules。
"""
import json, sys, time, os, glob
import requests
from playwright.sync_api import sync_playwright

CK_FILE = "/Users/lan/dsh/trae-recon/accounts/trae-ck.jsonl"
WW = "http://127.0.0.1:7866"
PROXY = sys.argv[1] if len(sys.argv) > 1 else None      # 例：socks5://127.0.0.1:39009
IDX = int(sys.argv[2]) if len(sys.argv) > 2 else 0

CH = glob.glob(os.path.expanduser(
    "~/Library/Caches/ms-playwright/chromium-*/chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing"))

rows = [json.loads(l) for l in open(CK_FILE, encoding="utf-8") if l.strip()]
ck = rows[IDX]
print(f"账号 {IDX}: {ck['email'][:16]}…  uid={ck['uid']}  region={ck.get('region')}")
print(f"  bd={ck.get('bd')} isLogin={ck.get('isLogin')} hasCloudide={ck.get('hasCloudideSession')}")
print(f"  cookies: {len(ck['cookies'])} 个")
print(f"  代理: {PROXY or '直连'}")
print()

# ── 1. 让 wild-work 起一个会话，拿 auth_url ──
requests.post(f"{WW}/api/login/cancel", timeout=10)
time.sleep(1.5)
r = requests.post(f"{WW}/api/login/start", json={"channel": "traework"}, timeout=25).json()
auth_url = r.get("auth_url", "")
if not auth_url:
    print("❌ 拿不到 auth_url:", r); sys.exit(1)
from urllib.parse import urlparse, parse_qs
print(f"授权 URL : {auth_url.split('?')[0]}")
print(f"回调     : {parse_qs(urlparse(auth_url).query).get('auth_callback_url',['?'])[0]}")
print(f"auth_from: {parse_qs(urlparse(auth_url).query).get('auth_from',['?'])[0]}")
print()

# ── 2. 起隔离浏览器，注入 CK ──
kw = {"headless": True, "args": ["--disable-blink-features=AutomationControlled"]}
if CH: kw["executable_path"] = CH[0]
with sync_playwright() as p:
    b = p.chromium.launch(**kw)
    ckw = {"locale": "en-US", "viewport": {"width": 1440, "height": 900}}
    if PROXY: ckw["proxy"] = {"server": PROXY}
    ctx = b.new_context(**ckw)

    cookies = [{"name": k, "value": v, "domain": ".trae.ai", "path": "/", "secure": True}
               for k, v in ck["cookies"].items()]
    ctx.add_cookies(cookies)
    print(f"注入 cookie: {len(cookies)} 个")

    pg = ctx.new_page()
    print("打开 www.trae.ai …")
    pg.goto("https://www.trae.ai/", wait_until="domcontentloaded", timeout=60000)
    pg.wait_for_timeout(6000)
    print(f"  落到: {pg.url[:100]}")

    # 问一下登录态
    chk = pg.evaluate("""async () => {
      try {
        const r = await fetch('https://ug-normal.us.trae.ai/cloudide/api/v3/trae/CheckLogin',
          {method:'POST', credentials:'include', headers:{'Content-Type':'application/json'}, body:'{}'});
        return await r.json();
      } catch(e) { return {err: String(e)}; }
    }""")
    res = (chk or {}).get("Result") or {}
    print(f"  IsLogin : {res.get('IsLogin')}  Region: {res.get('Region')}  UserID: {res.get('UserID')}")
    if chk.get("err"): print(f"  err: {chk['err'][:100]}")
    pg.screenshot(path="/tmp/trae-home.png")

    # ── 3. 打开授权页 ──
    print("\n打开授权页 …")
    try:
        pg.goto(auth_url, wait_until="domcontentloaded", timeout=60000)
    except Exception as e:
        print("  goto:", str(e)[:80])
    pg.wait_for_timeout(12000)
    print(f"  落到: {pg.url.replace('https://www.trae.ai','')[:120]}")

    txt = pg.evaluate("""() => {
      const t = [...document.querySelectorAll('div,span,a,button,h1,h2,p,[role=button]')]
        .map(x => (x.textContent||'').trim())
        .filter(s => s && s.length < 60);
      return [...new Set(t)].slice(0, 40);
    }""")
    print("  页面文本:")
    for s in txt: print(f"    · {s}")
    pg.screenshot(path="/tmp/trae-authz.png", full_page=True)
    print("\n截图: /tmp/trae-home.png  /tmp/trae-authz.png")
    b.close()
