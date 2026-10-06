# -*- coding: utf-8 -*-
"""端到端：美国区账号通过 auth_from=trae 拿到 AuthCode，直接喂给 wild-work 自己的回调。

关键点 —— **不改 wild-work 一行代码**：
  1. POST /api/login/start {channel:"traework"}  -> wild-work 起一个临时回调服务，
     返回的 auth_url 里带它自己的 auth_callback_url
  2. 只改三个参数再打开同一个授权页：
       auth_from      : solo        -> trae            (走 7093 路由，无 blockTTP 地区墙)
       login_channel  : native_ide  -> ai_extension    (2687 路由的自动流闸门)
       x_app_version  : 0.1.52      -> 99.99.99        (绕过 em===4「版本过低」分支)
     回调地址原样保留 wild-work 的
  3. CK 注入隔离浏览器，点「Log in and open TRAE」
  4. 浏览器跳到 wild-work 的 /authorize?...authCodeInfo=... → 它自己 ExchangeToken + 落盘

用法: .venv/bin/python trae-recon/probe/authz_e2e.py [socks5://host:port] [account_index]
"""
import json
import os
import sys
import time
import glob

import requests
from playwright.sync_api import sync_playwright

CK_FILE = "/Users/lan/dsh/trae-recon/accounts/trae-ck.jsonl"
WW = "http://127.0.0.1:7866"

PROXY = sys.argv[1] if len(sys.argv) > 1 else None
IDX = int(sys.argv[2]) if len(sys.argv) > 2 else 0
APP_VERSION = sys.argv[3] if len(sys.argv) > 3 else "99.99.99"


def state():
    try:
        return requests.get(f"{WW}/api/state", timeout=10).json()
    except Exception as e:
        return {"err": str(e)}


def trae_accounts(st):
    out = []
    for a in st.get("accounts", []) or []:
        if str(a.get("kind", "")).lower().startswith("trae"):
            out.append(a)
    return out


def main():
    rows = [json.loads(l) for l in open(CK_FILE, encoding="utf-8") if l.strip()]
    ck = rows[IDX]

    before = trae_accounts(state())
    print(f"账号   : {IDX} {ck['email'][:22]}… uid={ck['uid']} region={ck.get('region')}")
    print(f"代理   : {PROXY or '直连'}")
    print(f"登录前 trae 账号数: {len(before)} -> {[a.get('uid','')[:12] for a in before]}\n")

    requests.post(f"{WW}/api/login/cancel", timeout=10)
    time.sleep(1.5)
    r = requests.post(f"{WW}/api/login/start", json={"channel": "traework"}, timeout=25).json()
    auth_url = r.get("auth_url", "")
    if not auth_url:
        print("❌ 拿不到 auth_url:", r)
        sys.exit(1)

    from urllib.parse import urlparse, urlencode, urlunparse, parse_qsl
    u = urlparse(auth_url)
    q = dict(parse_qsl(u.query))
    ww_cb = q.get("auth_callback_url")
    print(f"wild-work 回调: {ww_cb}")
    print(f"  原参数: auth_from={q.get('auth_from')}  login_channel={q.get('login_channel')}"
          f"  x_app_version={q.get('x_app_version')}")

    q["auth_from"] = "trae"
    q["login_channel"] = "ai_extension"
    q["x_app_version"] = APP_VERSION
    url = urlunparse(u._replace(query=urlencode(q)))
    print(f"  新参数: auth_from=trae  login_channel=ai_extension  x_app_version={APP_VERSION}\n")

    ch = glob.glob(os.path.expanduser(
        "~/Library/Caches/ms-playwright/chromium-*/chrome-mac-arm64/"
        "Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing"))
    kw = {"headless": True, "args": ["--disable-blink-features=AutomationControlled"]}
    if ch:
        kw["executable_path"] = ch[0]

    with sync_playwright() as p:
        b = p.chromium.launch(**kw)
        ckw = {"locale": "en-US", "viewport": {"width": 1280, "height": 800}}
        if PROXY:
            ckw["proxy"] = {"server": PROXY, "bypass": "127.0.0.1,localhost"}
        ctx = b.new_context(**ckw)
        ctx.add_cookies([{"name": k, "value": v, "domain": ".trae.ai",
                          "path": "/", "secure": True} for k, v in ck["cookies"].items()])
        pg = ctx.new_page()

        print("导航授权页 …")
        pg.goto(url, wait_until="domcontentloaded", timeout=60000)

        # 等登录卡片
        txt = ""
        for i in range(25):
            pg.wait_for_timeout(1000)
            txt = pg.evaluate(
                "() => (document.body&&document.body.innerText||'').replace(/\\s+/g,' ').trim()")
            if "Log in and open" in txt or "Unavailable" in txt or "Authenticating" in txt:
                break
        print(f"  卡片: {txt[:120]}")

        if "Unavailable" in txt:
            print("❌ 仍是地区墙")
            b.close()
            return
        if "Log in and open" not in txt:
            print("⚠️  没看到登录按钮，继续尝试 …")

        # 点击提交按钮：取文本匹配里**面积最小**的（最内层），避免点到外层容器
        coords = pg.evaluate("""() => {
            let best = null;
            for (const el of document.querySelectorAll('button,a,div,span')) {
                const t = (el.innerText||'').trim();
                if (!t.startsWith('Log in and open') || t.length > 40) continue;
                const r = el.getBoundingClientRect();
                if (r.width <= 0 || r.height <= 0) continue;
                const area = r.width * r.height;
                if (!best || area < best.area)
                    best = {x: r.x + r.width/2, y: r.y + r.height/2, t, area,
                            tag: el.tagName, cls: (el.className||'').toString().slice(0,50)};
            }
            return best;
        }""")
        if not coords:
            print("❌ 找不到提交按钮")
            b.close()
            return
        print(f"点击 <{coords['tag']}.{coords['cls']}> {coords['t']!r} "
              f"@({coords['x']:.0f},{coords['y']:.0f}) area={coords['area']:.0f}")
        pg.mouse.click(coords["x"], coords["y"])

        caught = None
        for i in range(30):
            pg.wait_for_timeout(1000)
            cur = pg.url
            # 只有真的导航到本地才算命中（trae.ai 的 URL 里也含回调串，不能简单 in 判断）
            if cur.startswith("http://127.0.0.1:"):
                caught = cur
                print(f"🎯 {i+1}s 后回调命中: {cur[:100]}")
                break
            if "chrome-error" in cur:
                print(f"⚠️  {i+1}s 导航到本地失败: {cur}")
                break
        print(f"最终 URL: {pg.url[:130]}")
        b.close()

    if not caught:
        print("❌ 没等到回调")
        return

    # 轮询 wild-work 是否落盘
    print("\n等 wild-work 落盘 …")
    new = None
    for i in range(30):
        time.sleep(1)
        now = trae_accounts(state())
        added = [a for a in now if a.get("uid") not in {x.get("uid") for x in before}]
        if added:
            new = added
            print(f"✅ {i+1}s 后新账号入库")
            break
    st = state()
    print(f"  login_busy : {st.get('login_busy')}")
    print(f"  login_error: {st.get('login_error')}")
    now = trae_accounts(st)
    print(f"  trae 账号数: {len(before)} -> {len(now)}")
    for a in now:
        if a.get("uid") not in {x.get("uid") for x in before}:
            print("\n  ★ 新账号:")
            for k in ("uid", "nickname", "email", "kind", "credits", "disabled", "expiresAt", "lastCheckinOk"):
                if k in a:
                    print(f"      {k:14s}= {a[k]}")
    if not new:
        print("  （未检测到新账号，看上面 login_error）")


if __name__ == "__main__":
    main()
