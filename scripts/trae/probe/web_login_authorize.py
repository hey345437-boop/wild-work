# -*- coding: utf-8 -*-
"""网页版登录 → 跳授权页 → 你在窗口里点「授权」。

上一轮的教训：我一直在用 IDE 的邮箱登录，那条路有域名风控；
而 Trae 真正的登录路径是**网页版**——相同域名相同密码，网页版实测 200 放行。

流程：
  1. 可见 chromium（可选代理）+ 本地回调服务
  2. 在 www.trae.ai/login 填邮箱密码并点「Log in」
  3. 验证登录态（CheckLogin）
  4. 跳授权页，把 URL 打出来；窗口留给你点「授权」

用法:
  .venv/bin/python trae-recon/probe/web_login_authorize.py <邮箱> <密码> [代理]
"""
import glob
import json
import os
import sys
import threading
import http.server
import socketserver
from urllib.parse import urlparse, urlencode, urlunparse, parse_qsl

import requests
from playwright.sync_api import sync_playwright

EMAIL = sys.argv[1] if len(sys.argv) > 1 else "2tpskvgzlf@ruutukf.com"
PW = sys.argv[2] if len(sys.argv) > 2 else "Trae6eqkfrwf!9"
PROXY = sys.argv[3] if len(sys.argv) > 3 else "socks5://127.0.0.1:39042"

WW = "http://127.0.0.1:7866"
CLIENT_IDE = "ono9krqynydwx5"
CAUGHT = []


class CB(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def do_GET(self):
        CAUGHT.append(self.path)
        p = ("<html><body style='font:18px -apple-system,sans-serif;padding:48px;background:#111;color:#eee'>"
             "<h2>✅ 已收到授权回调</h2><p>可以关掉这个标签页了。</p></body></html>").encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(p)))
        self.end_headers()
        self.wfile.write(p)

    def log_message(self, *a):
        pass


def main():
    srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), CB)
    srv.daemon_threads = True
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    cb = f"http://127.0.0.1:{port}/authorize"

    # 授权 URL 模板（含 PKCE / 设备号 / 版本号）
    requests.post(f"{WW}/api/login/cancel", timeout=10)
    import time
    time.sleep(1.2)
    r = requests.post(f"{WW}/api/login/start", json={"channel": "traework"}, timeout=25).json()
    auth_url = r.get("auth_url", "")
    if not auth_url:
        print("❌ 拿不到 auth_url:", r)
        srv.shutdown()
        return
    u = urlparse(auth_url)
    q = dict(parse_qsl(u.query))
    q["client_id"] = CLIENT_IDE
    q["auth_from"] = "trae"
    q["login_channel"] = "ai_extension"
    q["auth_callback_url"] = cb
    authz_url = urlunparse(u._replace(query=urlencode(q)))

    print("=" * 74)
    print(f"邮箱     : {EMAIL}")
    print(f"代理     : {PROXY or '直连'}")
    print(f"本地回调 : {cb}")
    print("=" * 74)
    print("\n授权链接（复制备用）：\n")
    print(authz_url)
    print()

    ch = glob.glob(os.path.expanduser(
        "~/Library/Caches/ms-playwright/chromium-*/chrome-mac-arm64/"
        "Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing"))
    kw = {"headless": False, "args": ["--disable-blink-features=AutomationControlled",
                                      "--window-size=1280,940"]}
    if ch:
        kw["executable_path"] = ch[0]

    with sync_playwright() as p:
        b = p.chromium.launch(**kw)
        ckw = {"locale": "zh-CN", "viewport": {"width": 1280, "height": 900}}
        if PROXY:
            ckw["proxy"] = {"server": PROXY, "bypass": "127.0.0.1,localhost"}
        ctx = b.new_context(**ckw)
        pg = ctx.new_page()

        # ── 1. 网页版登录 ──
        print("① 打开网页版登录页 …")
        pg.goto("https://www.trae.ai/login", wait_until="domcontentloaded", timeout=60000)
        pg.wait_for_timeout(8000)
        try:
            pg.fill("input[type=email]", EMAIL)
            pg.wait_for_timeout(600)
            pg.fill("input[type=password]", PW)
            pg.wait_for_timeout(600)
            box = pg.evaluate("""() => {
                let b=null;
                for (const el of document.querySelectorAll('div,span,a,button,[role=button]')) {
                    const t=(el.textContent||'').trim();
                    if (t!=='Log in' && t!=='登录') continue;
                    const r=el.getBoundingClientRect(); if(r.width<5||r.height<5) continue;
                    const a=r.width*r.height; if(!b||a<b.a)
                        b={x:Math.round(r.x+r.width/2),y:Math.round(r.y+r.height/2),a};
                }
                return b;
            }""")
            if box:
                print(f"   点「登入」@({box['x']},{box['y']})")
                pg.mouse.move(box["x"], box["y"]); pg.wait_for_timeout(150)
                pg.mouse.down(); pg.wait_for_timeout(80); pg.mouse.up()
            else:
                pg.keyboard.press("Enter")
            print("   等待登录响应 …")
            pg.wait_for_timeout(14000)
        except Exception as e:
            print("   表单异常:", str(e)[:110])

        # ── 2. 验登录态 ──
        print("\n② 检查登录态 …")
        pg.goto("https://www.trae.ai/", wait_until="domcontentloaded", timeout=60000)
        pg.wait_for_timeout(6000)
        st = pg.evaluate("""async () => {
          try {
            const c = await fetch('https://ug-normal.us.trae.ai/cloudide/api/v3/trae/CheckLogin',
              {method:'POST',credentials:'include',headers:{'Content-Type':'application/json'},body:'{}'});
            return ((await c.json()).Result)||{};
          } catch(e) { return {err:String(e)}; }
        }""")
        print(f"   IsLogin={st.get('IsLogin')}  Region={st.get('Region')}  UID={st.get('UserID')}")
        if not st.get("IsLogin"):
            print("\n   ⚠️ 没登上 —— 你可以在窗口里手动登，登完告诉我，我再去点授权")
        else:
            print(f"   ✅ 已登录（区域 {st.get('Region')}）")

        # ── 3. 跳授权页 ──
        print("\n③ 跳授权页 …")
        try:
            pg.goto(authz_url, wait_until="domcontentloaded", timeout=60000)
        except Exception as e:
            print("   goto:", str(e)[:90])

        print("\n" + "=" * 74)
        print("  窗口已停在授权页。请你确认页面并点「Log in and open TRAE」")
        print("  我在这等回调，最多 10 分钟。")
        print("=" * 74 + "\n")

        last = ""
        for i in range(600):
            pg.wait_for_timeout(1000)
            if CAUGHT:
                print(f"\n🎯 授权回调到了！({i}s)")
                break
            if i % 8 == 0:
                try:
                    t = pg.evaluate("()=>document.body.innerText.replace(/\\s+/g,' ').trim().slice(0,120)")
                except Exception:
                    t = "<页面跳走>"
                if t != last:
                    print(f"  [{i:3d}s] {pg.url[:96]}")
                    print(f"          {t}")
                    last = t
                    try:
                        pg.screenshot(path="/tmp/trae-authorize.png")
                    except Exception:
                        pass
        b.close()
    srv.shutdown()

    if not CAUGHT:
        print("\n❌ 10 分钟没收到回调")
        return
    qs = dict(parse_qsl(urlparse(CAUGHT[0]).query))
    print(f"\n回调参数: {list(qs)}")
    if "authCodeInfo" in qs:
        info = json.loads(qs["authCodeInfo"])
        print(f"  AuthCode   = {info.get('AuthCode')}")
        print(f"  host       = {qs.get('host')}")
        print(f"  userRegion = {qs.get('userRegion')}")
        print(f"  userInfo   = {str(qs.get('userInfo'))[:150]}")
        json.dump({"email": EMAIL, "authCode": info.get("AuthCode"), "host": qs.get("host"),
                   "userRegion": qs.get("userRegion")},
                  open("/tmp/trae-authcode.json", "w"), indent=2, ensure_ascii=False)
        print("\n已存 /tmp/trae-authcode.json")


if __name__ == "__main__":
    main()
