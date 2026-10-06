# -*- coding: utf-8 -*-
"""auth_from=trae 全流程验证：渲染登录卡片 → 点击 → 接住本地回调。

矩阵测试结论（同账号、同 CK、同 client_id，只变 auth_from）：
    solo / native_ide   -> 地区墙 "TraeWork Unavailable"    (chunk 9934, blockTTP=true)
    trae                -> "Log in to TRAE Desktop App"     (chunk 7093, 无 blockTTP) ★

本脚本在 trae 路由上继续走：修好本地 HTTP 服务（原先缺 Content-Length 导致
ERR_EMPTY_RESPONSE），加版本参数绕过 em===4 的「版本过低」分支，然后点击提交按钮。

用法: .venv/bin/python trae-recon/probe/authz_trae.py [socks5://host:port] [account_index] [app_version]
"""
import json
import os
import sys
import time
import glob
import threading
import http.server
import socketserver
from urllib.parse import urlparse, urlencode, urlunparse, parse_qsl

import requests
from playwright.sync_api import sync_playwright

CK_FILE = "/Users/lan/dsh/trae-recon/accounts/trae-ck.jsonl"
WW = "http://127.0.0.1:7866"

PROXY = sys.argv[1] if len(sys.argv) > 1 else None
IDX = int(sys.argv[2]) if len(sys.argv) > 2 else 0
APP_VERSION = sys.argv[3] if len(sys.argv) > 3 else "99.99.99"

HITS = []
NET = []


class CB(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"          # 关键：不发 Content-Length 也能正确收尾

    def _r(self, m):
        body = b""
        if m == "POST":
            n = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(n) if n else b""
        HITS.append((m, self.path, body.decode("utf-8", "replace")))
        payload = b"<html><body>ok</body></html>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Access-Control-Allow-Private-Network", "true")
        self.end_headers()
        self.wfile.write(payload)
        self.wfile.flush()

    def do_GET(self):
        self._r("GET")

    def do_POST(self):
        self._r("POST")

    def do_OPTIONS(self):
        self._r("OPTIONS")

    def log_message(self, *a):
        pass


def main():
    rows = [json.loads(l) for l in open(CK_FILE, encoding="utf-8") if l.strip()]
    ck = rows[IDX]

    srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), CB)
    srv.daemon_threads = True
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    cb = f"http://127.0.0.1:{port}/authorize"

    print(f"账号   : {IDX} {ck['email'][:22]}… uid={ck['uid']} region={ck.get('region')}")
    print(f"代理   : {PROXY or '直连'}")
    print(f"回调   : {cb}")
    print(f"x_app_version 覆盖为: {APP_VERSION}\n")

    requests.post(f"{WW}/api/login/cancel", timeout=10)
    time.sleep(1.2)
    r = requests.post(f"{WW}/api/login/start", json={"channel": "traework"}, timeout=25).json()
    if not r.get("auth_url"):
        print("❌", r)
        sys.exit(1)
    u = urlparse(r["auth_url"])
    q = dict(parse_qsl(u.query))
    q["auth_from"] = "trae"
    q["login_channel"] = "ai_extension"
    q["auth_callback_url"] = cb
    q["x_app_version"] = APP_VERSION
    url = urlunparse(u._replace(query=urlencode(q)))
    print(f"auth_from={q['auth_from']}  login_channel={q['login_channel']}\n")

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

        # 自检：本地回调服务是否真的能应答
        try:
            selftest = requests.get(cb, timeout=5)
            print(f"回调自检: HTTP {selftest.status_code} ({len(selftest.text)}B)")
        except Exception as e:
            print(f"回调自检失败: {e}")
        HITS.clear()

        pg.on("request", lambda r: NET.append(("REQ", r.url[:120], "")) if "127.0.0.1" in r.url else None)
        pg.on("requestfailed", lambda r: NET.append(("FAIL", r.url[:110], str(r.failure)[:80])))
        ctx.on("page", lambda np: NET.append(("POPUP", np.url[:110], "")))

        def on_resp(resp):
            if any(s in resp.url for s in ("GetRefreshToken", "ExchangeToken",
                                           "GetPCAuthCode", "required_version", "GetUserInfo")):
                try:
                    txt = resp.text()[:220]
                except Exception:
                    txt = "?"
                NET.append((resp.status, resp.url.split("?")[0][-60:], txt))

        pg.on("response", on_resp)

        pg.goto(url, wait_until="domcontentloaded", timeout=60000)

        # 等渲染稳定
        for i in range(20):
            pg.wait_for_timeout(1000)
            if HITS:
                break
            t = pg.evaluate("() => (document.body&&document.body.innerText||'').replace(/\\s+/g,' ').trim()")
            if t and "Authenticating" not in t:
                break

        print(f"[渲染 8s] url={pg.url.replace('https://www.trae.ai','')[:80]}")
        body = pg.evaluate("() => (document.body&&document.body.innerText||'').replace(/\\s+/g,' ').trim()")
        print(f"          body: {body[:220]}")

        # 找可点击元素：按钮可能是 div/a，不是 <button>
        dom = pg.evaluate("""() => {
            const out = [];
            for (const el of document.querySelectorAll('button,a,div[role=button],[class*=btn],.trae__btn')) {
                const t = (el.innerText || '').trim();
                if (!t || t.length > 40) continue;
                const r = el.getBoundingClientRect();
                if (r.width === 0 || r.height === 0) continue;
                out.push({ t, tag: el.tagName, cls: (el.className || '').toString().slice(0, 60),
                           x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2) });
            }
            return out;
        }""")
        print(f"          可点元素: {[(d['tag'], d['t']) for d in dom]}")

        sub = [d for d in dom if "cancel" not in d["t"].lower()]
        if sub and not HITS:
            tgt = sub[0]
            print(f"\n点击: {tgt['t']!r} @({tgt['x']},{tgt['y']})")
            try:
                pg.mouse.click(tgt["x"], tgt["y"])
            except Exception as e:
                print("  mouse click err:", str(e)[:90])
                try:
                    pg.get_by_text(tgt["t"], exact=True).first.click(timeout=8000)
                except Exception as e2:
                    print("  text click err:", str(e2)[:90])
            for i in range(30):
                pg.wait_for_timeout(1000)
                if HITS:
                    print(f"  🎯 回调命中 ({i+1}s)")
                    break
        else:
            # 无按钮：等自动流程
            for i in range(30):
                pg.wait_for_timeout(1000)
                if HITS:
                    print(f"  🎯 自动流程回调命中 ({i+1}s)")
                    break

        print(f"\n最终 URL: {pg.url[:120]}")
        body = pg.evaluate("() => (document.body&&document.body.innerText||'').replace(/\\s+/g,' ').trim()")
        print(f"最终正文: {body[:220]}")

        print("\n" + "=" * 72)
        if HITS:
            print("✅ 本地回调被触发 —— 拿到凭据：")
            for m, path, bd in HITS:
                print(f"\n  {m} {path[:110]}")
                if m == "POST":
                    try:
                        for k, v in json.loads(bd).items():
                            vs = str(v)
                            print(f"    {k:16s}= {vs[:90]}{'…' if len(vs)>90 else ''}")
                    except Exception:
                        print("   ", bd[:400])
                else:
                    from urllib.parse import urlparse as up, parse_qsl as pq
                    for k, v in pq(up(path).query):
                        vs = str(v)
                        print(f"    {k:16s}= {vs[:90]}{'…' if len(vs)>90 else ''}")
        else:
            print("❌ 未触发回调")

        if NET:
            print("\n-- 关键请求 --")
            for st, pth, bd in NET[-10:]:
                print(f"  {st}  {pth}")
                if st != 200:
                    print(f"       {bd[:200]}")

        pg.screenshot(path="/tmp/trae-authz-trae.png", full_page=True)
        print("\n截图: /tmp/trae-authz-trae.png")
        b.close()
    srv.shutdown()


if __name__ == "__main__":
    main()
